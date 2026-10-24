"""Copies of a backup store into another (app/core/backup_store_sync.py; docs/design/backup-store.md, step 4): a copy
store made from its source (same chunk size, same key), backups copied chunk by chunk as they are, only the chunks the
copy lacks, a retention of the copy's own that a deletion at the source never reaches, and the recovery of a lost
site from a store."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core import backup_store, backup_store_sync, backup_stores, site_recovery
from app.core.database import get_conn

CHUNK = 64 * 1024
VM = str(uuid.uuid4())


@pytest.fixture()
def pools(tmp_path, monkeypatch):
    found = {"local": tmp_path / "local", "remote": tmp_path / "remote"}
    for path in found.values():
        path.mkdir()
    monkeypatch.setattr(backup_stores, "_pool_dir", lambda name: found[name])
    # The copy's pool is a mount point another site's share would be: recovery reads under the pools' directories.
    monkeypatch.setattr(site_recovery, "_allowed_roots", lambda: [str(p) for p in found.values()])
    return found


def _stamp(days_ago):
    return (datetime(2026, 10, 24, 2, 0, tzinfo=UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backup(store, stamp, fill, vm="web"):
    data = bytes([fill]) * (3 * CHUNK) + b"\x07" * CHUNK  # the last chunk the same in every backup
    index = store.write_disk(lambda o, n: data[o : o + n], len(data))
    return store.write_backup(VM, stamp, {"vda": index}, {"vm": vm, "mode": "live"}, {"vm-config.json": b"{}"})


def _stores(encrypted=False):
    source, _ = backup_stores.create("main", "local", encrypted=encrypted)
    target, _ = backup_stores.create("offsite", "remote", copy_of="main")
    return backup_stores.open_store("main"), backup_stores.open_store("offsite"), source, target


def _job(**changes):
    job = {"name": "nightly-copy", "source": "main", "target": "offsite", "frequency": "daily", "time": "04:00",
           "retention_count": 3, **changes}  # fmt: skip
    return backup_store_sync.save_job(job)


@pytest.mark.parametrize("encrypted", [False, True])
def test_a_copy_store_shares_its_sources_chunks_and_key(database, pools, encrypted):
    source, target, source_row, target_row = _stores(encrypted)
    assert target_row["copy_of"] == "main" and target_row["encrypted"] == source_row["encrypted"]
    assert target.same_chunks_as(source) and target.id != source.id
    directory = _backup(source, _stamp(0), 1)
    copied = target.copy_backup_from(backup_stores.open_store("main", raw=True), VM, _stamp(0))
    assert copied == 1  # the disk fits in one chunk of the store (4 MiB)
    assert target.copy_backup_from(source, VM, _stamp(0)) == 0  # already there
    assert target.verify(VM, _stamp(0)) == []  # readable with the key the copy shares
    assert target.read_file(VM, _stamp(0), "vm-config.json") == b"{}"
    assert sorted(p.name for p in target.backup_dir(VM, _stamp(0)).iterdir()) == sorted(
        p.name for p in directory.iterdir()
    )


def test_a_damaged_chunk_is_never_copied(database, pools):
    source, target, _, _ = _stores()
    _backup(source, _stamp(0), 1)
    digest = source.index_of(VM, _stamp(0), "vda").digests[0]
    raw = bytearray(source.chunk_path(digest).read_bytes())
    raw[-1] ^= 0xFF
    source.chunk_path(digest).write_bytes(bytes(raw))
    with pytest.raises(backup_store.DamagedChunk, match="CRC"):
        target.copy_backup_from(source, VM, _stamp(0))
    assert target.backups() == []


def test_the_copy_keeps_its_own_retention_whatever_the_source_deletes(database, pools):
    source, target, _, _ = _stores(encrypted=True)
    for day in range(5, 0, -1):
        _backup(source, _stamp(day), day)
    job = _job()
    result = backup_store_sync.run_job(job)
    assert (result["copied"], result["removed"]) == (3, 0)  # what its policy keeps of five: never copies to delete
    assert [b[1] for b in target.backups()] == [_stamp(3), _stamp(2), _stamp(1)]
    # The source loses its backups (its retention, a mistake, an attack): the copy keeps its own.
    for _vm, stamp, _ in source.backups():
        source.delete_backup(VM, stamp)
    assert backup_store_sync.run_job(job)["removed"] == 0
    assert len(target.backups()) == 3
    _backup(source, _stamp(0), 9)  # a new backup: copied, and the copy's oldest goes by its policy
    result = backup_store_sync.run_job(job)
    assert (result["copied"], result["removed"]) == (1, 1)
    assert [b[1] for b in target.backups()] == [_stamp(2), _stamp(1), _stamp(0)]
    # The copy's side checks what it holds without the key: every chunk's CRC.
    raw = backup_stores.open_store("offsite", raw=True)
    assert raw.verify_raw(VM, _stamp(0)) == []


def test_a_job_needs_a_copy_store_of_its_source(database, pools):
    backup_stores.create("main", "local")
    backup_stores.create("other", "remote")
    with pytest.raises(backup_store_sync.SyncError, match="not a copy of 'main'"):
        _job(target="other")
    with pytest.raises(backup_store_sync.SyncError, match="another store"):
        _job(target="main")
    backup_stores.create("offsite", "remote", "copy", copy_of="main")
    job = _job()
    with pytest.raises(backup_stores.StoreError, match="copy job nightly-copy"):
        backup_stores.remove("offsite")
    with pytest.raises(backup_stores.StoreError, match="its copy"):
        backup_stores.remove("main")
    backup_store_sync.delete_job(job["id"])
    backup_stores.remove("offsite")


def test_the_api_runs_a_copy_job_in_a_task(client, auth_headers, pools, monkeypatch):
    from app.routers import backups as router

    admin = auth_headers("ana")
    assert client.post("/backup-stores", json={"name": "main", "pool": "local"}, headers=admin).status_code == 201
    r = client.post("/backup-stores", json={"name": "offsite", "pool": "remote", "copy_of": "main"}, headers=admin)
    assert r.status_code == 201 and r.json()["copy_of"] == "main"
    _backup(backup_stores.open_store("main"), _stamp(0), 1)
    body = {"name": "nightly-copy", "source": "main", "target": "offsite", "frequency": "daily", "time": "04:00"}
    job = client.post("/backup-store-syncs", json=body, headers=admin).json()
    assert client.post("/backup-store-syncs", json=body, headers=admin).status_code == 409
    assert client.get("/backup-store-syncs", headers=admin).json()["total"] == 1

    class Threads:
        def Thread(self, target, **kwargs):
            return type("T", (), {"start": lambda s: target()})()

    monkeypatch.setattr(router, "threading", Threads())
    r = client.post(f"/backup-store-syncs/{job['id']}/run", headers=admin)
    assert r.status_code == 202
    done = client.get(f"/backup-store-syncs/{job['id']}", headers=admin).json()
    assert done["last_result"].startswith("1 backups copied") and done["last_run"]
    viewer = auth_headers("vic", role="viewer")
    assert client.get("/backup-store-syncs", headers=viewer).status_code == 403


def test_a_lost_sites_vms_are_recovered_from_a_store(database, pools):
    source, target, _, _ = _stores()
    _backup(source, _stamp(1), 1, vm="web")
    _backup(source, _stamp(0), 2, vm="web")
    job = _job()
    backup_store_sync.run_job(job)
    vms = site_recovery._scan_store(target.root, {})
    assert [v["vm"] for v in vms] == ["web"]
    newest = vms[0]["backups"][0]
    assert newest["created_at"] == _stamp(0) and newest["source"] == "offsite (copy of main)" and newest["disks"] == 1
    backup_id = site_recovery.register(newest["path"], "ana")
    assert site_recovery.register(newest["path"], "ana") == backup_id  # once
    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone())
    assert row["store"] == "offsite" and row["status"] == "succeeded" and row["size_bytes"] == 4 * CHUNK


def test_a_store_this_cluster_does_not_know_is_not_read(database, pools):
    stray = backup_store.create(pools["remote"] / "stray")
    _backup(stray, _stamp(0), 1)
    with pytest.raises(site_recovery.RecoveryError, match="does not know"):
        site_recovery._scan_store(stray.root, {})
