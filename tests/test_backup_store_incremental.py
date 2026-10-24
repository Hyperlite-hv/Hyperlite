"""Incremental reads into a backup store (docs/design/backup-store.md, step 2), without QEMU: what libvirt is asked
(a pull-mode job counting from a checkpoint, the next checkpoint), how the dirty bitmap becomes chunks to read, how an
index is built on the previous one, and when a backup may read only the changes. tests/test_backup_store_qemu.py runs
the same on a real VM."""

import io
import json
import subprocess
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest

from app.core import backup_store, backup_store_vm

CHUNK = 64 * 1024
VM = str(uuid.uuid4())


def _disk(dev, fmt="qcow2", block=False):
    return backup_store_vm.Disk(dev, f"/dev/{dev}" if block else f"/nonexistent/{dev}.{fmt}", fmt)


def test_the_job_counts_from_the_checkpoint_and_reads_the_others_whole(tmp_path):
    disks = [_disk("vda"), _disk("vdb", "raw")]
    root = ET.fromstring(
        backup_store_vm._pull_xml(disks, tmp_path / "s.sock", tmp_path, ["sdz"], "hl-store-x-1", {"vdb"})
    )
    assert root.findtext("incremental") == "hl-store-x-1"
    vda, vdb, sdz = root.findall("disks/disk")
    assert vda.get("exportbitmap") == "dirty-vda" and vda.get("backupmode") is None
    assert vdb.get("backupmode") == "full" and vdb.get("exportbitmap") is None
    assert sdz.get("backup") == "no"
    plain = ET.fromstring(backup_store_vm._pull_xml(disks, tmp_path / "s.sock", tmp_path, []))
    assert plain.find("incremental") is None and all(d.get("exportbitmap") is None for d in plain.findall("disks/disk"))
    checkpoint = ET.fromstring(backup_store_vm._checkpoint_xml("hl-store-x-2", disks, {"vda"}))
    assert checkpoint.findtext("name") == "hl-store-x-2"
    assert [(d.get("name"), d.get("checkpoint")) for d in checkpoint.findall("disks/disk")] == [
        ("vda", "bitmap"),
        ("vdb", "no"),
    ]


def test_the_dirty_extents_become_the_chunks_to_read(monkeypatch):
    extents = [
        {"offset": 0, "length": CHUNK, "type": 0, "description": "clean"},
        {"offset": CHUNK, "length": 10, "type": 1, "description": "dirty"},  # one byte range inside chunk 1
        {"offset": CHUNK + 10, "length": 3 * CHUNK, "type": 0, "description": "clean"},
        {"offset": 4 * CHUNK - 1, "length": CHUNK + 2, "type": 1, "description": "dirty"},  # across chunks 3, 4, 5
    ]
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(extents), "")

    monkeypatch.setattr(backup_store_vm.subprocess, "run", run)
    assert backup_store_vm.changed_chunks("nbd+unix:///vda?socket=/s", "vda", 10 * CHUNK, CHUNK) == {1, 3, 4, 5}
    assert seen[0][1] == "--map=qemu:dirty-bitmap:dirty-vda"
    monkeypatch.setattr(
        backup_store_vm.subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1, "", "no such context")
    )
    with pytest.raises(backup_store_vm.BackupFailed, match="changed blocks of vda"):
        backup_store_vm.changed_chunks("uri", "vda", CHUNK, CHUNK)


class _Ranges:
    """Stands for backup_store_nbd_read: serves the asked ranges of `disk`, in order."""

    def __init__(self, disk):
        self.disk, self.asked = disk, []

    def __call__(self, uri, ranges):
        self.asked = list(ranges)
        data = b"".join(self.disk[o : o + n] for o, n in ranges)
        process = type("P", (), {"stdout": io.BytesIO(data), "stderr": io.BytesIO(b""), "wait": lambda s, t=None: 0})()
        listing = type("L", (), {"unlink": lambda s, missing_ok=False: None})()
        return process, listing


def test_an_index_is_built_on_the_previous_one_and_a_lost_chunk_is_read_again(tmp_path, monkeypatch):
    store = backup_store.create(tmp_path / "s", chunk_size=CHUNK)
    old = bytes([1]) * CHUNK + bytes([2]) * CHUNK + bytes([3]) * CHUNK + bytes([4]) * (CHUNK // 2)
    base = store.write_disk(lambda o, n: old[o : o + n], len(old))
    new = old[:CHUNK] + bytes([9]) * CHUNK + old[2 * CHUNK :]
    ranges = _Ranges(new)
    monkeypatch.setattr(backup_store_vm, "_read_ranges", ranges)
    index, read = backup_store_vm.read_changed_into_store(store, "uri", len(new), base, {1})
    assert read == 1 and ranges.asked == [(CHUNK, CHUNK)]
    assert index.digests[0] == base.digests[0] and index.digests[1] != base.digests[1]
    out = bytearray(len(new))
    store.read_disk(index, lambda o, c: out.__setitem__(slice(o, o + len(c)), c))
    assert bytes(out) == new
    # A chunk of the base the store lost (garbage collection, damage) is read again rather than named.
    store.chunk_path(base.digests[3]).unlink()
    index, read = backup_store_vm.read_changed_into_store(store, "uri", len(new), base, set())
    assert read == 1 and ranges.asked == [(3 * CHUNK, CHUNK // 2)]
    assert store.chunk_path(index.digests[3]).exists()


class _Domain:
    def __init__(self, active=True, checkpoints=()):
        self.active, self._checkpoints = active, list(checkpoints)

    def isActive(self):
        return self.active

    def UUIDString(self):
        return VM

    def name(self):
        return "web"

    def listAllCheckpoints(self):
        return [type("C", (), {"getName": lambda s, n=n: n})() for n in self._checkpoints]


def _recorded(store, stamp, **manifest):
    index = store.write_disk(lambda o, n: b"\x01" * n, CHUNK)
    store.write_backup(VM, stamp, {"vda": index}, manifest)


def test_a_backup_reads_only_the_changes_when_its_base_is_sound(tmp_path):
    store = backup_store.create(tmp_path / "s", chunk_size=CHUNK)
    recent = datetime.now(UTC).isoformat()
    assert backup_store_vm._incremental_base(_Domain(), store) is None  # nothing before
    _recorded(store, "t1", checkpoint="c1", full_at=recent)
    _recorded(store, "t2", full_at=recent)  # the VM was stopped: no checkpoint of its own
    stamp, manifest = backup_store_vm._incremental_base(_Domain(checkpoints=["c1"]), store)
    assert (stamp, manifest["checkpoint"]) == ("t1", "c1")
    assert backup_store_vm._incremental_base(_Domain(active=False, checkpoints=["c1"]), store) is None
    assert backup_store_vm._incremental_base(_Domain(checkpoints=[]), store) is None  # dropped by a snapshot, a move
    old = (datetime.now(UTC) - timedelta(days=backup_store_vm.FULL_EVERY_DAYS + 1)).isoformat()
    _recorded(store, "t3", checkpoint="c3", full_at=old)
    assert backup_store_vm._incremental_base(_Domain(checkpoints=["c3"]), store) is None  # time for a whole read
