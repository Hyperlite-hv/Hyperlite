"""The backup store (app/core/backup_store.py, docs/design/backup-store.md): a disk written then read back bit for bit,
chunks shared by every backup that holds the same content, encryption that refuses a wrong key and catches tampering,
verification that finds a damaged chunk and the next backup that repairs it, and garbage collection that frees only
what no backup uses, even while a backup runs."""

import hashlib
import json
import os
import time
import uuid

import pytest

from app.core import backup_store as bs

CHUNK = 64 * 1024  # small chunks: the same code, faster tests
VM = str(uuid.uuid4())


@pytest.fixture()
def store(tmp_path):
    return bs.create(tmp_path / "store", chunk_size=CHUNK)


def _disk(size, seed=1, zero_from=None, zero_to=None):
    data = bytearray()
    block = hashlib.sha256(str(seed).encode()).digest()
    while len(data) < size:
        block = hashlib.sha256(block).digest()
        data += block
    data = data[:size]
    if zero_from is not None:
        data[zero_from:zero_to] = bytes(zero_to - zero_from)
    return bytes(data)


def _write(store, data):
    return store.write_disk(lambda off, n: data[off : off + n], len(data))


def _read(store, index):
    out = bytearray(index.size)

    def put(off, chunk):
        out[off : off + len(chunk)] = chunk

    store.read_disk(index, put)
    return bytes(out)


def _chunks(store):
    return sorted(p for p in (store.root / bs.CHUNKS).rglob("*") if p.is_file())


def _backup(store, stamp, disks):
    indexes = {name: _write(store, data) for name, data in disks.items()}
    return store.write_backup(VM, stamp, indexes, {"vm": "web", "mode": "live"}, files={"nvram": b"vars"})


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 5 * CHUNK + 123])
def test_a_disk_reads_back_bit_for_bit(store, size):
    data = _disk(size)
    index = _write(store, data)
    assert index.size == size and len(index.digests) == -(-size // CHUNK)
    assert _read(store, index) == data
    assert bs.Index.decode(index.encode()) == index


def test_zeros_are_never_stored_and_equal_content_is_stored_once(store):
    data = _disk(6 * CHUNK, zero_from=CHUNK, zero_to=3 * CHUNK)
    index = _write(store, data)
    assert index.digests[1] == index.digests[2] == bs.ZERO
    assert len(_chunks(store)) == 4
    _write(store, data)  # a second VM made from the same template
    assert len(_chunks(store)) == 4
    assert _read(store, index) == data


def test_compressible_chunks_are_compressed_and_random_ones_kept_as_they_are(store):
    text = (b"hyperlite " * CHUNK)[:CHUNK]
    noise = os.urandom(CHUNK)
    small, raw = store.chunk_path(store.put(text)), store.chunk_path(store.put(noise))
    assert small.stat().st_size < CHUNK // 10
    assert raw.stat().st_size == CHUNK + 16  # the header only
    assert store.get(store.digest(text)) == text and store.get(store.digest(noise)) == noise


def test_a_backup_exists_once_its_manifest_is_written(store):
    _backup(store, "2026-10-24T10:00:00Z", {"vda": _disk(3 * CHUNK), "vdb": _disk(CHUNK, seed=2)})
    half = store.backup_dir(VM, "2026-10-24T11:00:00Z")
    half.mkdir(parents=True)
    (half / "vda.fidx").write_bytes(b"cut")  # a backup that stopped before its manifest
    [(vm, stamp, manifest)] = store.backups()
    assert (vm, stamp) == (VM, "2026-10-24T10:00:00Z")
    assert set(manifest["disks"]) == {"vda", "vdb"} and manifest["store"] == store.id
    assert (store.backup_dir(VM, stamp) / "nvram").read_bytes() == b"vars"
    assert _read(store, store.index_of(VM, stamp, "vda")) == _disk(3 * CHUNK)


def test_an_index_that_does_not_match_its_manifest_is_refused(store):
    directory = _backup(store, "t1", {"vda": _disk(2 * CHUNK)})
    other = _write(store, _disk(2 * CHUNK, seed=9)).encode()
    (directory / "vda.fidx").write_bytes(other)
    with pytest.raises(ValueError, match="does not match the manifest"):
        store.index_of(VM, "t1", "vda")
    with pytest.raises(ValueError, match="checksum"):
        bs.Index.decode(other[:-1] + bytes([other[-1] ^ 1]))


def test_names_cannot_leave_the_backups_directory(store):
    for name in ("../x", "a/b", "", "..", "manifest.json"):
        with pytest.raises(ValueError):
            store.write_backup(VM, f"t-{len(name)}-{name.count('.')}", {name: _write(store, b"")}, {})


def test_verification_finds_a_damaged_chunk_and_the_next_backup_repairs_it(store):
    data = _disk(3 * CHUNK)
    _backup(store, "t1", {"vda": data})
    assert store.verify(VM, "t1") == []
    digest = store.index_of(VM, "t1", "vda").digests[1]
    path = store.chunk_path(digest)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    assert store.verify(VM, "t1") == [digest]
    assert not path.exists() and path.with_name(path.name + ".bad").exists()
    _backup(store, "t2", {"vda": data})  # the same content again: the chunk is written anew
    assert store.verify(VM, "t1") == [] and _read(store, store.index_of(VM, "t1", "vda")) == data


def test_a_missing_chunk_is_reported_and_no_backup_is_recorded_without_its_chunks(store):
    _backup(store, "t1", {"vda": _disk(2 * CHUNK)})
    index = store.index_of(VM, "t1", "vda")
    store.chunk_path(index.digests[0]).unlink()
    assert store.verify(VM, "t1") == [index.digests[0]]
    with pytest.raises(bs.MissingChunk):
        store.write_backup(VM, "t2", {"vda": index}, {})  # an incremental that reused a chunk swept meanwhile
    assert [b[1] for b in store.backups()] == ["t1"]


def test_an_encrypted_store_needs_its_key_and_hides_the_content(tmp_path):
    key = bs.new_key()
    store = bs.create(tmp_path / "enc", encrypted_with=key, chunk_size=CHUNK)
    data = _disk(2 * CHUNK)
    index = _write(store, data)
    assert index.digests[0] != hashlib.sha256(data[:CHUNK]).digest()  # a digest says nothing of the content
    assert data[:64] not in store.chunk_path(index.digests[0]).read_bytes()
    assert _read(bs.Store(store.root, key), index) == data
    with pytest.raises(bs.StoreError, match="its key is needed"):
        bs.Store(store.root)
    with pytest.raises(bs.StoreError, match="not the store's key"):
        bs.Store(store.root, bs.new_key())
    assert "key" not in json.loads((store.root / bs.CONFIG).read_text())  # only a fingerprint


def test_an_encrypted_chunk_tampered_with_or_moved_is_caught(tmp_path):
    store = bs.create(tmp_path / "enc", encrypted_with=bs.new_key(), chunk_size=CHUNK)
    a, b = store.put(_disk(CHUNK, seed=1)), store.put(_disk(CHUNK, seed=2))
    pa, pb = store.chunk_path(a), store.chunk_path(b)
    pb.write_bytes(pa.read_bytes())  # chunk a passed off as chunk b, CRC and all
    with pytest.raises(bs.DamagedChunk, match="authentication failed"):
        store.get(b)
    raw = bytearray(pa.read_bytes())
    raw[40] ^= 1
    import zlib

    raw[12:16] = zlib.crc32(bytes(raw[16:])).to_bytes(4, "big")  # a tampering that keeps the CRC right
    pa.write_bytes(bytes(raw))
    with pytest.raises(bs.DamagedChunk, match="authentication failed"):
        store.get(a)


def test_garbage_collection_frees_only_what_no_backup_uses(store):
    shared = _disk(CHUNK, seed=7)
    _backup(store, "t1", {"vda": shared + _disk(CHUNK, seed=1)})
    _backup(store, "t2", {"vda": shared + _disk(CHUNK, seed=2)})
    for path in _chunks(store):
        _age(path, 3 * 24 * 3600)
    store.delete_backup(VM, "t1")
    result = store.collect_garbage()
    assert result["removed"] == 1 and result["kept"] == 2
    assert store.verify(VM, "t2") == []


def test_garbage_collection_keeps_a_recent_chunk_no_backup_names_yet(store):
    """A backup running meanwhile: its chunks are written before its index, so no index names them during the mark."""
    digest = store.put(_disk(CHUNK, seed=3))
    assert store.collect_garbage()["removed"] == 0
    assert store.chunk_path(digest).exists()


def test_a_chunk_reused_by_a_backup_during_the_sweep_is_kept(store, monkeypatch):
    """A backup finds a chunk already there and touches it while the sweep is about to delete it."""
    digest = store.put(_disk(CHUNK, seed=4))
    path = store.chunk_path(digest)
    _age(path, 3 * 24 * 3600)
    real = os.replace

    def replace_then_touch(src, dst):
        real(src, dst)
        if str(dst).endswith(".sweep"):
            # The backup's touch lands after the rename: it finds nothing and writes the chunk again.
            assert store.put(_disk(CHUNK, seed=4)) == digest

    monkeypatch.setattr(bs.os, "replace", replace_then_touch)
    store.collect_garbage()
    monkeypatch.setattr(bs.os, "replace", real)
    assert store.get(digest) == _disk(CHUNK, seed=4)


def test_a_touch_just_before_the_rename_sends_the_chunk_back(store, monkeypatch):
    digest = store.put(_disk(CHUNK, seed=5))
    path = store.chunk_path(digest)
    _age(path, 3 * 24 * 3600)
    real_stat = type(path).stat
    calls = {"n": 0}

    def stat_then_touch(self, *a, **k):
        st = real_stat(self, *a, **k)
        if self == path and calls["n"] == 0:
            calls["n"] += 1
            os.utime(path)  # the backup's touch, between the sweep's check and its rename
        return st

    monkeypatch.setattr(type(path), "stat", stat_then_touch)
    assert store.collect_garbage()["removed"] == 0
    monkeypatch.undo()
    assert path.exists() and not path.with_name(path.name + ".sweep").exists()


def test_a_collection_that_stopped_midway_leaves_nothing_behind(store):
    digest = store.put(_disk(CHUNK, seed=6))
    path = store.chunk_path(digest)
    os.replace(path, path.with_name(path.name + ".sweep"))  # stopped between its rename and its check
    store.collect_garbage()
    assert path.exists() and not path.with_name(path.name + ".sweep").exists()  # recent: back and kept


def test_garbage_collection_stops_on_an_unreadable_backup(store):
    directory = _backup(store, "t1", {"vda": _disk(CHUNK)})
    (directory / "vda.fidx").write_bytes(b"damaged")
    for path in _chunks(store):
        _age(path, 3 * 24 * 3600)
    with pytest.raises(bs.StoreError, match="unreadable"):
        store.collect_garbage()
    assert len(_chunks(store)) == 1  # nothing swept on a guess


def test_a_store_is_made_only_in_an_empty_directory_that_keeps_access_times(tmp_path, monkeypatch):
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "file").write_text("x")
    with pytest.raises(bs.StoreError, match="not empty"):
        bs.create(tmp_path / "used")
    real = os.utime
    calls = []

    def utime_ignoring_now(path, times=None):
        calls.append(times)
        if times is not None:
            real(path, times)  # the old time sticks: the share ignores the touch, as one mounted with noatime may

    monkeypatch.setattr(bs.os, "utime", utime_ignoring_now)
    with pytest.raises(bs.StoreError, match="access times"):
        bs.create(tmp_path / "noatime")
    monkeypatch.undo()
    with pytest.raises(bs.StoreError, match="not a backup store"):
        bs.Store(tmp_path / "noatime")
