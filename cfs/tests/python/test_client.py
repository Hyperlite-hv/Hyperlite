"""The Python client against the real hyperlite-cfs daemon, started on a temporary socket and database.

The daemon is the binary built by Meson; HYPERLITE_CFS_BIN names it. Without it the tests are skipped, so the backend
suite never needs a C toolchain (the CI's cfs job builds it and runs this file)."""

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core import cfs_client
from app.core.cfs_client import CfsClient, CfsError, Conflict, Locked, NotFound

BIN = os.environ.get("HYPERLITE_CFS_BIN", "")

pytestmark = pytest.mark.skipif(not BIN or not Path(BIN).is_file(), reason="HYPERLITE_CFS_BIN is not built")


def start(tmp_path, db=None):
    sock = tmp_path / "cfs.sock"
    proc = subprocess.Popen(
        [BIN, "--db", str(db or tmp_path / "config.db"), "--socket", str(sock)],
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(200):
        if sock.exists():
            return proc, str(sock)
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read())
        time.sleep(0.02)
    proc.kill()
    raise RuntimeError("hyperlite-cfs did not create its socket")


def stop(proc):
    proc.terminate()
    assert proc.wait(timeout=10) == 0


@pytest.fixture()
def daemon(tmp_path):
    proc, sock = start(tmp_path)
    yield sock
    stop(proc)


def test_files_versions_and_compare_and_set(daemon):
    with CfsClient(daemon) as c:
        v1 = c.put("/nodes/pve1/qemu/100.json", b'{"name": "web"}', expected=cfs_client.MUST_NOT_EXIST)
        entry = c.get("/nodes/pve1/qemu/100.json")
        assert entry.data == b'{"name": "web"}' and entry.version == v1 and entry.mtime > 0

        v2 = c.put("/nodes/pve1/qemu/100.json", b'{"name": "web", "cores": 4}', expected=v1)
        with pytest.raises(Conflict):
            c.put("/nodes/pve1/qemu/100.json", b"stale", expected=v1)
        assert v2 > v1

        assert [(ch.name, ch.is_dir) for ch in c.list("/nodes/pve1")] == [("qemu", True)]
        assert c.rename("/nodes/pve1/qemu/100.json", "/nodes/pve2/qemu/100.json", expected=v2) > v2
        with pytest.raises(NotFound):
            c.get("/nodes/pve1/qemu/100.json")
        c.delete("/nodes/pve2/qemu/100.json")
        assert c.list("/") == []


def test_ids_locks_and_status(daemon):
    with CfsClient(daemon) as c:
        assert [c.next_id(), c.next_id()] == [100, 101]
        c.lock("vm:100", "migrate@pve1", ttl=30)
        with pytest.raises(Locked) as held:
            c.lock("vm:100", "backup@pve2")
        assert held.value.reason == "migrate@pve1"
        c.unlock("vm:100", "migrate@pve1")
        c.lock("vm:100", "backup@pve2")

        status = c.status()
        assert status.mode == "local" and status.quorate and len(status.checksum) == 64
        assert status.entries == 0 and status.version == 2


def test_refusals_carry_a_reason(daemon):
    with CfsClient(daemon) as c:
        with pytest.raises(CfsError) as bad:
            c.put("/a/../etc/passwd", b"x")
        assert bad.value.status == cfs_client.INVALID and bad.value.reason == "invalid path"
        c.put("/cluster/settings.json", b"{}")
        with pytest.raises(CfsError) as shape:
            c.put("/cluster/settings.json/x", b"x")
        assert "is a file" in shape.value.reason
        with pytest.raises(CfsError) as big:
            c.put("/big", b"x" * (1024 * 1024 + 1))
        assert big.value.status == cfs_client.TOO_LARGE


def test_priv_is_root_only(daemon):
    with CfsClient(daemon) as c:
        if os.geteuid() == 0:
            c.put("/priv/authkey", b"k")
            assert c.get("/priv/authkey").data == b"k"
        else:
            with pytest.raises(CfsError) as denied:
                c.put("/priv/authkey", b"k")
            assert denied.value.status == cfs_client.FORBIDDEN


def test_concurrent_clients_get_unique_ids_and_one_winner(daemon):
    ids, wins, errors = [], [], []

    def worker():
        try:
            with CfsClient(daemon) as c:
                for _ in range(50):
                    ids.append(c.next_id())
                try:
                    c.put("/race", b"mine", expected=cfs_client.MUST_NOT_EXIST)
                    wins.append(1)
                except Conflict:
                    pass
        except Exception as e:  # collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(ids) == 400 and len(set(ids)) == 400
    assert len(wins) == 1


def test_data_survives_a_restart(tmp_path):
    proc, sock = start(tmp_path)
    with CfsClient(sock) as c:
        c.put("/cluster/settings.json", b'{"keyboard": "fr"}')
        c.next_id()
        before = c.status()
    stop(proc)

    proc, sock = start(tmp_path)
    try:
        with CfsClient(sock) as c:
            assert c.get("/cluster/settings.json").data == b'{"keyboard": "fr"}'
            after = c.status()
            assert (after.version, after.checksum) == (before.version, before.checksum)
            assert c.next_id() == 101
    finally:
        stop(proc)


def test_a_garbage_frame_drops_only_that_connection(daemon):
    import socket
    import struct

    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.connect(daemon)
    raw.sendall(struct.pack("<I", 0x7FFFFFFF))  # a length over the frame limit
    raw.settimeout(5)
    assert raw.recv(16) == b""  # closed by the daemon
    raw.close()
    with CfsClient(daemon) as c:
        assert c.status().mode == "local"
