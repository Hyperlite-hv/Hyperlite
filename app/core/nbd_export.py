"""A backup in a store served as a disk (docs/design/live-restore.md, part 2): a read-only NBD server on a Unix socket,
which reads each 4 MiB chunk of the disk's index from the store (decrypted when the store is), as Proxmox Backup
Server serves a backup to a live restore.

QEMU opens it as the backing file of the new VM's disk (nbd+unix:///?socket=...); the VM boots on it, and libvirt's block
pull copies the rest. The protocol as the NBD specification (docs/proto.md of the NBD project) gives it, the least of
it: fixed newstyle negotiation, NBD_OPT_GO, NBD_OPT_INFO and NBD_OPT_EXPORT_NAME, simple replies only (structured
replies and metadata contexts are refused, which every client falls back from), and NBD_CMD_READ and NBD_CMD_DISC;
every write is refused (EPERM). Each connection is served by a thread of its own; the chunks last read are kept, a block
pull and the guest's reads both read on.
"""

import contextlib
import logging
import os
import socket
import struct
import threading
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

NBDMAGIC = 0x4E42444D41474943
IHAVEOPT = 0x49484156454F5054
OPT_REPLY_MAGIC = 0x3E889045565A9
REQUEST_MAGIC = 0x25609513
SIMPLE_REPLY_MAGIC = 0x67446698

FLAG_FIXED_NEWSTYLE, FLAG_NO_ZEROES = 1, 2
OPT_EXPORT_NAME, OPT_ABORT, OPT_INFO, OPT_GO = 1, 2, 6, 7
REP_ACK, REP_INFO = 1, 3
REP_ERR_UNSUP, REP_ERR_INVALID = 2**31 + 1, 2**31 + 3
INFO_EXPORT = 0
# HAS_FLAGS, READ_ONLY, CAN_MULTI_CONN: nothing changes the data, so connections may read in parallel.
TRANSMISSION_FLAGS = 1 | 2 | 256
CMD_READ, CMD_DISC = 0, 2
EPERM, EIO, EINVAL = 1, 5, 22
MAX_READ = 32 * 1024 * 1024  # what a client may ask at once (the specification's usual limit)
MAX_OPTION = 64 * 1024
CACHED_CHUNKS = 16


class _Closed(Exception):
    pass


class IndexServer:
    """Serve `index` (backup_store.Index), read from `store` (backup_store.Store), at `path` until close()."""

    def __init__(self, store, index, path, owner=None):
        self.store, self.index, self.path = store, index, Path(path)
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._conns = set()
        self._closing = False
        self.path.unlink(missing_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        if owner:  # QEMU's user and group: it connects to it
            os.chown(self.path, *owner)
        os.chmod(self.path, 0o660)
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._accept, name="nbd-export", daemon=True)
        self._thread.start()

    @property
    def uri(self):
        return f"nbd+unix:///?socket={self.path}"

    def close(self):
        self._closing = True
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()
        for conn in list(self._conns):
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        self._thread.join(5)
        self.path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the data ----

    def _chunk(self, i):
        with self._lock:
            if i in self._cache:
                self._cache.move_to_end(i)
                return self._cache[i]
        size = min(self.index.chunk_size, self.index.size - i * self.index.chunk_size)
        data = self.store.get(self.index.digests[i], size)
        with self._lock:
            self._cache[i] = data
            while len(self._cache) > CACHED_CHUNKS:
                self._cache.popitem(last=False)
        return data

    def read(self, offset, length):
        """`length` bytes from `offset`, from the chunks they fall in. A damaged chunk raises (the client gets EIO)."""
        out = bytearray()
        size = self.index.chunk_size
        while length > 0:
            i, within = divmod(offset, size)
            piece = self._chunk(i)[within : within + length]
            if not piece:
                raise ValueError("read past the chunk")
            out += piece
            offset += len(piece)
            length -= len(piece)
        return bytes(out)

    # ---- the protocol ----

    def _accept(self):
        while not self._closing:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._conns.add(conn)
            threading.Thread(target=self._serve, args=(conn,), name="nbd-conn", daemon=True).start()

    def _serve(self, conn):
        try:
            with conn:
                if self._negotiate(conn):
                    self._transmit(conn)
        except (_Closed, OSError):
            pass
        except Exception:
            logger.exception("NBD export %s: a connection failed", self.path)
        finally:
            self._conns.discard(conn)

    def _negotiate(self, conn):
        conn.sendall(struct.pack(">QQH", NBDMAGIC, IHAVEOPT, FLAG_FIXED_NEWSTYLE | FLAG_NO_ZEROES))
        (client_flags,) = struct.unpack(">I", _recv(conn, 4))
        if not client_flags & FLAG_FIXED_NEWSTYLE:
            return False
        no_zeroes = bool(client_flags & FLAG_NO_ZEROES)
        while True:
            magic, option, length = struct.unpack(">QII", _recv(conn, 16))
            if magic != IHAVEOPT or length > MAX_OPTION:
                return False
            data = _recv(conn, length)
            if option == OPT_EXPORT_NAME:  # the old way: no reply header, the export's size and flags at once
                conn.sendall(
                    struct.pack(">QH", self.index.size, TRANSMISSION_FLAGS) + (b"" if no_zeroes else bytes(124))
                )
                return True
            if option == OPT_ABORT:
                _reply(conn, option, REP_ACK)
                return False
            if option in (OPT_INFO, OPT_GO):
                if len(data) < 6 or 4 + struct.unpack(">I", data[:4])[0] + 2 > len(data):
                    _reply(conn, option, REP_ERR_INVALID)
                    continue
                _reply(conn, option, REP_INFO, struct.pack(">HQH", INFO_EXPORT, self.index.size, TRANSMISSION_FLAGS))
                _reply(conn, option, REP_ACK)
                if option == OPT_GO:
                    return True
                continue
            _reply(conn, option, REP_ERR_UNSUP)  # structured replies, metadata contexts, lists, TLS...

    def _transmit(self, conn):
        while True:
            magic, _flags, kind, cookie, offset, length = struct.unpack(">IHHQQI", _recv(conn, 28))
            if magic != REQUEST_MAGIC:
                return
            if kind == CMD_DISC:
                return
            if kind == 1:  # NBD_CMD_WRITE carries its data: read it before refusing
                _recv(conn, length)
            if kind != CMD_READ:
                conn.sendall(struct.pack(">IIQ", SIMPLE_REPLY_MAGIC, EPERM if kind in (1, 4, 6) else EINVAL, cookie))
                continue
            if length > MAX_READ or offset + length > self.index.size:
                conn.sendall(struct.pack(">IIQ", SIMPLE_REPLY_MAGIC, EINVAL, cookie))
                continue
            try:
                data = self.read(offset, length)
            except Exception:
                logger.exception("NBD export %s: reading %d bytes at %d failed", self.path, length, offset)
                conn.sendall(struct.pack(">IIQ", SIMPLE_REPLY_MAGIC, EIO, cookie))
                continue
            conn.sendall(struct.pack(">IIQ", SIMPLE_REPLY_MAGIC, 0, cookie) + data)


def _recv(conn, n):
    buf = bytearray()
    while len(buf) < n:
        part = conn.recv(n - len(buf))
        if not part:
            raise _Closed()
        buf += part
    return bytes(buf)


def _reply(conn, option, kind, data=b""):
    conn.sendall(struct.pack(">QIII", OPT_REPLY_MAGIC, option, kind, len(data)) + data)
