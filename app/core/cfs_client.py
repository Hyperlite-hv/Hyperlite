"""Client of hyperlite-cfs, the replicated cluster configuration (docs/design/hyperlite-cfs.md).

The daemon listens on a Unix socket and speaks the framed binary protocol described in cfs/src/proto.h. This module is
its only Python speaker. Shadow mode (app/repositories/cfs/shadow.py, phase C of the design) uses it to copy writes
into the daemon; the repositories will read from it once the daemon becomes the source of truth (phase D).
"""

import socket
import struct
import threading
from dataclasses import dataclass

DEFAULT_SOCKET = "/run/hyperlite-cfs/socket"

ANY_VERSION = -1
MUST_NOT_EXIST = 0

# Wire values; they mirror enum cfs_status and enum cfs_op in cfs/src/cfs.h.
OK, NOT_FOUND, CONFLICT, INVALID, TOO_LARGE, FORBIDDEN, LOCKED, READ_ONLY, SYNCHRONISING, INTERNAL = range(10)
UNCERTAIN = 10
_GET, _PUT, _DELETE, _LIST, _RENAME, _LOCK, _UNLOCK, _NEXT_ID, _STATUS = range(1, 10)

FRAME_MAX = 1024 * 1024 + 4096


class CfsError(Exception):
    """A refusal from the daemon; `status` is one of the wire values above."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class NotFound(CfsError):
    pass


class Conflict(CfsError):
    """The expected version did not match: someone else changed the entry first."""


class Locked(CfsError):
    """The lock is held; `reason` names the holder."""


class ReadOnly(CfsError):
    """No quorum: this node refuses every change (cluster mode)."""


class Synchronising(CfsError):
    """The members are agreeing on one state after a membership change: retry shortly (cluster mode). The reason says
    when they hold different states, which only a state transfer resolves."""


class Uncertain(CfsError):
    """The membership changed before every member confirmed the change: it may or may not have been applied. Read the
    entry back before retrying; never retry blindly (cluster mode)."""


_ERRORS = {
    NOT_FOUND: NotFound,
    CONFLICT: Conflict,
    LOCKED: Locked,
    READ_ONLY: ReadOnly,
    SYNCHRONISING: Synchronising,
    UNCERTAIN: Uncertain,
}


@dataclass(frozen=True)
class Entry:
    data: bytes
    version: int
    mtime: int


@dataclass(frozen=True)
class Child:
    name: str
    is_dir: bool
    version: int
    size: int


@dataclass(frozen=True)
class Status:
    version: int
    checksum: str
    quorate: bool
    mode: str
    entries: int
    bytes: int


def _string(value):
    raw = value.encode() if isinstance(value, str) else bytes(value)
    return struct.pack("<I", len(raw)) + raw


class _Reader:
    def __init__(self, buf):
        self.buf = buf
        self.off = 0

    def take(self, n):
        if self.off + n > len(self.buf):
            raise CfsError(INTERNAL, "truncated answer from hyperlite-cfs")
        chunk = self.buf[self.off : self.off + n]
        self.off += n
        return chunk

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def i64(self):
        return struct.unpack("<q", self.take(8))[0]

    def blob(self):
        return self.take(self.u32())


class CfsClient:
    """One connection, used by one request at a time (a lock serialises callers sharing the client)."""

    def __init__(self, path=DEFAULT_SOCKET, timeout=10.0):
        self.path = path
        self.timeout = timeout
        self._sock = None
        self._next_id = 0
        self._lock = threading.Lock()

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _connect(self):
        if self._sock is None:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(self.path)
            except OSError:
                sock.close()
                raise
            self._sock = sock
        return self._sock

    def _recv_exact(self, n):
        chunks = []
        while n:
            chunk = self._sock.recv(min(n, 65536))
            if not chunk:
                raise ConnectionError("hyperlite-cfs closed the connection")
            chunks.append(chunk)
            n -= len(chunk)
        return b"".join(chunks)

    def _call(self, op, payload=b""):
        with self._lock:
            self._next_id = (self._next_id + 1) & 0xFFFFFFFF
            request_id = self._next_id
            body = struct.pack("<BI", op, request_id) + payload
            sock = self._connect()
            try:
                sock.sendall(struct.pack("<I", len(body)) + body)
                (length,) = struct.unpack("<I", self._recv_exact(4))
                if length > FRAME_MAX:
                    raise ConnectionError("hyperlite-cfs sent an oversized answer")
                answer = self._recv_exact(length)
            except (OSError, ConnectionError):
                # A half-read answer leaves the stream out of step: start again on a new connection next time.
                self.close()
                raise
        r = _Reader(answer)
        if r.u32() != request_id:
            self.close()
            raise CfsError(INTERNAL, "answer to another request")
        status = r.u8()
        if status != OK:
            reason = r.blob().decode(errors="replace")
            raise _ERRORS.get(status, CfsError)(status, reason)
        return r

    def get(self, path):
        r = self._call(_GET, _string(path))
        version, mtime = r.i64(), r.i64()
        return Entry(data=bytes(r.blob()), version=version, mtime=mtime)

    def put(self, path, data, expected=ANY_VERSION):
        """Write `data`; `expected` is ANY_VERSION, MUST_NOT_EXIST (create only) or the version read before.
        Returns the new version."""
        return self._call(_PUT, _string(path) + struct.pack("<q", expected) + _string(data)).i64()

    def delete(self, path, expected=ANY_VERSION):
        self._call(_DELETE, _string(path) + struct.pack("<q", expected))

    def list(self, path="/"):
        r = self._call(_LIST, _string(path))
        children = []
        for _ in range(r.u32()):
            name = r.blob().decode()
            is_dir = bool(r.u8())
            children.append(Child(name=name, is_dir=is_dir, version=r.i64(), size=r.i64()))
        return children

    def rename(self, source, target, expected=ANY_VERSION):
        return self._call(_RENAME, _string(source) + _string(target) + struct.pack("<q", expected)).i64()

    def lock(self, name, owner, ttl=60):
        self._call(_LOCK, _string(name) + _string(owner) + struct.pack("<I", ttl))

    def unlock(self, name, owner):
        self._call(_UNLOCK, _string(name) + _string(owner))

    def next_id(self):
        return self._call(_NEXT_ID).i64()

    def status(self):
        r = self._call(_STATUS)
        version = r.i64()
        checksum = bytes(r.blob()).hex()
        quorate = bool(r.u8())
        mode = "local" if r.u8() == 0 else "cluster"
        return Status(version=version, checksum=checksum, quorate=quorate, mode=mode, entries=r.i64(), bytes=r.i64())
