"""A backup in a store served as a disk (app/core/nbd_export.py): the NBD negotiation and reads a client does, every
write refused, a damaged chunk an I/O error; nbdcopy reading the whole disk back when libnbd's tools are installed."""

import shutil
import socket
import struct
import subprocess

import pytest

from app.core import backup_store, nbd_export

CHUNK = 64 * 1024


class Store:
    """backup_store.Store as the server reads it: a chunk by its digest, zeros for the zero digest."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0

    def get(self, digest, size=None):
        self.reads += 1
        if digest == backup_store.ZERO:
            return bytes(size)
        if digest not in self.chunks:
            raise backup_store.MissingChunk(digest)
        return self.chunks[digest]


@pytest.fixture()
def served(tmp_path):
    data = bytes(range(256)) * (CHUNK // 256)
    other = b"\x5a" * (CHUNK // 2)  # the last chunk, shorter
    store = Store({b"a" * 32: data, b"b" * 32: other})
    index = backup_store.Index(2 * CHUNK + CHUNK // 2, CHUNK, (b"a" * 32, backup_store.ZERO, b"b" * 32))
    disk = data + bytes(CHUNK) + other
    with nbd_export.IndexServer(store, index, tmp_path / "s.sock") as server:
        yield server, disk, store


def _connect(path):
    """The negotiation of a fixed-newstyle client asking for structured replies (refused) then NBD_OPT_GO."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(path))
    magic, opt, flags = struct.unpack(">QQH", s.recv(18))
    assert (magic, opt) == (nbd_export.NBDMAGIC, nbd_export.IHAVEOPT) and flags & 1
    s.sendall(struct.pack(">I", 3))
    s.sendall(struct.pack(">QII", nbd_export.IHAVEOPT, 8, 0))  # NBD_OPT_STRUCTURED_REPLY
    assert struct.unpack(">QIII", _recv(s, 20))[2] == nbd_export.REP_ERR_UNSUP
    s.sendall(struct.pack(">QII", nbd_export.IHAVEOPT, nbd_export.OPT_GO, 6) + struct.pack(">IH", 0, 0))
    _, _, kind, length = struct.unpack(">QIII", _recv(s, 20))
    assert kind == nbd_export.REP_INFO
    info, size, tflags = struct.unpack(">HQH", _recv(s, length))
    assert info == nbd_export.INFO_EXPORT
    assert struct.unpack(">QIII", _recv(s, 20))[2] == nbd_export.REP_ACK
    return s, size, tflags


def _recv(s, n):
    buf = b""
    while len(buf) < n:
        part = s.recv(n - len(buf))
        assert part
        buf += part
    return buf


def _request(s, kind, offset, length, cookie=7, payload=b""):
    s.sendall(struct.pack(">IHHQQI", nbd_export.REQUEST_MAGIC, 0, kind, cookie, offset, length) + payload)
    magic, error, got = struct.unpack(">IIQ", _recv(s, 16))
    assert magic == nbd_export.SIMPLE_REPLY_MAGIC and got == cookie
    return error, (_recv(s, length) if error == 0 and kind == nbd_export.CMD_READ else b"")


def test_a_client_reads_the_disk_across_its_chunks_and_never_writes(served):
    server, disk, store = served
    s, size, flags = _connect(server.path)
    assert size == len(disk) and flags & 2  # read-only
    assert _request(s, 0, 0, len(disk)) == (0, disk)
    assert _request(s, 0, CHUNK - 10, 20) == (0, disk[CHUNK - 10 : CHUNK + 10])
    reads = store.reads
    assert _request(s, 0, 5, 5)[1] == disk[5:10] and store.reads == reads  # the chunk read last is kept
    assert _request(s, 1, 0, 4, payload=b"evil")[0] == nbd_export.EPERM
    assert _request(s, 0, len(disk) - 1, 2)[0] == nbd_export.EINVAL  # past the end
    assert _request(s, 0, 0, 4) == (0, disk[:4])  # still served after a refusal
    s.sendall(struct.pack(">IHHQQI", nbd_export.REQUEST_MAGIC, 0, nbd_export.CMD_DISC, 0, 0, 0))
    s.close()


def test_a_missing_chunk_is_an_io_error_never_wrong_data(tmp_path):
    index = backup_store.Index(CHUNK, CHUNK, (b"c" * 32,))
    with nbd_export.IndexServer(Store({}), index, tmp_path / "s.sock") as server:
        s, _, _ = _connect(server.path)
        assert _request(s, 0, 0, 16)[0] == nbd_export.EIO
        s.close()
    assert not (tmp_path / "s.sock").exists()


@pytest.mark.skipif(not shutil.which("nbdcopy"), reason="libnbd-bin is not installed")
def test_nbdcopy_reads_it_back_whole(served, tmp_path):
    server, disk, _ = served
    subprocess.run(["nbdcopy", "--", server.uri, str(tmp_path / "copy.raw")], check=True, timeout=60)
    assert (tmp_path / "copy.raw").read_bytes() == disk
