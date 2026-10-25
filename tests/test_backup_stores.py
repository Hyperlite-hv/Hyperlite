"""Backup stores in Hyperlite (app/core/backup_stores.py, docs/design/backup-store.md, step 1): a store made in a
directory of a storage pool, or taken over where one already is, reachable only where its pool is the same share;
the API that makes, lists and forgets them; backups into a store chosen by a manual backup, a schedule or a grouped
job; and the verification, retention and deletion of a backup kept in a store. libvirt is not needed: the pool's
directory is a temporary one, and a VM's backup is written into the store directly."""

import json
import uuid

import pytest

from app.core import backup_integrity, backup_store, backup_stores, backups
from app.core.database import get_conn

VM = str(uuid.uuid4())


@pytest.fixture()
def pools(tmp_path, monkeypatch):
    """Storage pools of files: {pool name: its directory on this node}."""
    found = {"nas": tmp_path / "nas", "local": tmp_path / "local"}
    for path in found.values():
        path.mkdir()

    def pool_dir(name):
        if name not in found:
            raise backup_stores.StoreError(f"The storage pool '{name}' is not on this node", 404)
        return found[name]

    monkeypatch.setattr(backup_stores, "_pool_dir", pool_dir)
    return found


class _Threads:
    """Stands for one module's threading: the threads it starts run as `start` says, every other thread is real."""

    def __init__(self, start):
        import threading

        self.Lock, self.Event, self._start = threading.Lock, threading.Event, start

    def Thread(self, target, **kwargs):
        start = self._start
        return type("Thread", (), {"start": lambda _self: start(target)})()


def _backup_into(store, stamp, data=b"\x01" * 5000):
    index = store.write_disk(lambda off, n: data[off : off + n], len(data))
    return store.write_backup(VM, stamp, {"vda": index}, {"vm": "web"})


def _row(path, created_at, vm="web", store="main"):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backups (vm_name, path, status, created_at, mode, store) VALUES (?, ?, 'succeeded', ?, 'live', ?)",
            (vm, str(path), created_at, store),
        )
        conn.commit()
        return cur.lastrowid


def test_a_store_is_made_in_a_pool_and_found_again_by_its_id(database, pools):
    row, _ = backup_stores.create("main", "nas", username="ana")
    assert (row["pool"], row["directory"]) == ("nas", "hyperlite-store")
    store = backup_stores.open_store("main")
    assert store.root == pools["nas"] / "hyperlite-store" and store.id == row["store_id"]
    described = backup_stores.describe(row)
    assert described["available"] and described["backups"] == 0 and described["free_bytes"] > 0
    with pytest.raises(backup_stores.StoreError, match="already exists"):
        backup_stores.create("main", "local")


def test_a_store_already_in_a_pool_is_taken_over_once(database, pools):
    made = backup_store.create(pools["nas"] / "kept")  # a store left by a reinstallation, or another cluster's
    row, _ = backup_stores.create("again", "nas", "kept")
    assert row["store_id"] == made.id
    with pytest.raises(backup_stores.StoreError, match="already a store of this cluster"):
        backup_stores.create("twice", "nas", "kept")


def test_another_share_mounted_at_the_pool_is_never_taken_for_the_store(database, pools):
    row, _ = backup_stores.create("main", "nas")
    other = pools["nas"] / "hyperlite-store"
    for path in sorted(other.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    backup_store.create(other)  # another store at the same place: the pool is not the same share here
    with pytest.raises(backup_stores.StoreError, match="holds another backup store"):
        backup_stores.open_store("main")
    assert not backup_stores.describe(row)["available"]


def test_a_pool_that_is_not_here_or_a_bad_name_is_refused(database, pools):
    with pytest.raises(backup_stores.StoreError, match="not on this node"):
        backup_stores.create("main", "elsewhere")
    for name in ("", "a/b", "../x", "x" * 64):
        with pytest.raises(backup_stores.StoreError, match="Invalid name"):
            backup_stores.create(name, "nas")
    with pytest.raises(backup_stores.StoreError, match="Invalid directory"):
        backup_stores.create("main", "nas", "../etc")


def test_a_store_a_job_uses_is_not_removed_and_its_files_stay(database, pools):
    backup_stores.create("main", "nas")
    from app.services import backup_service

    backup_service.set_schedule("web", "daily", "02:00", "", 7, None, None, None, "2026-01-01T02:00:00", "main")
    with pytest.raises(backup_stores.StoreError, match="schedule of web"):
        backup_stores.remove("main")
    backup_service.delete_schedule("web")
    backup_stores.remove("main")
    assert (pools["nas"] / "hyperlite-store" / backup_store.CONFIG).exists()
    with pytest.raises(backup_stores.StoreError, match="No backup store"):
        backup_stores.get("main")


def test_a_backup_in_a_store_is_verified_kept_by_retention_and_deleted(database, pools):
    backup_stores.create("main", "nas")
    store = backup_stores.open_store("main")
    paths = [_backup_into(store, f"2026-01-0{i}T02:00:00Z", bytes([i]) * 5000) for i in (1, 2, 3)]
    assert backup_integrity.verify(paths[0])[0] == backup_integrity.VERIFIED
    ids = [_row(p, f"2026-01-0{i}T02:00:00+00:00") for i, p in zip((1, 2, 3), paths, strict=True)]
    backups._apply_retention("web", {"last": 1, "daily": None, "weekly": None, "monthly": None})
    assert [b[1] for b in store.backups()] == ["2026-01-03T02:00:00Z"]
    with get_conn() as conn:
        assert [r["id"] for r in conn.execute("SELECT id FROM backups")] == [ids[2]]
    # A damaged chunk is found by the same verification every backup gets (manual, weekly, before a restore).
    digest = store.index_of(VM, "2026-01-03T02:00:00Z", "vda").digests[0]
    store.chunk_path(digest).write_bytes(b"damaged")
    status, problems, _ = backup_integrity.verify(paths[2])
    assert status == backup_integrity.CORRUPT and "damaged or missing" in problems[0]
    backups.remove_backup_files(paths[2])
    assert store.backups() == []


def test_garbage_collection_records_when_and_where_it_ran(database, pools, monkeypatch):
    backup_stores.create("main", "nas")
    store = backup_stores.open_store("main")
    _backup_into(store, "t1")
    started = []
    monkeypatch.setattr(backup_stores, "threading", _Threads(lambda target: started.append(target)))
    backup_stores.collect_due()
    assert len(started) == 1
    started[0]()  # the collection itself
    done = backup_stores.last_gc(store)
    assert done["node"] and done["removed"] == 0 and done["kept"] == 1
    started.clear()
    backup_stores.collect_due()  # collected less than a week ago: not again
    assert started == []


def test_the_api_makes_lists_and_forgets_stores(client, auth_headers, pools):
    admin = auth_headers("ana")
    r = client.post("/backup-stores", json={"name": "main", "pool": "nas"}, headers=admin)
    assert r.status_code == 201 and r.json()["available"]
    assert client.post("/backup-stores", json={"name": "main", "pool": "nas"}, headers=admin).status_code == 409
    assert client.post("/backup-stores", json={"name": "x", "pool": "gone"}, headers=admin).status_code == 404
    listed = client.get("/backup-stores", headers=admin).json()
    assert [s["name"] for s in listed["items"]] == ["main"] and listed["total"] == 1
    viewer = auth_headers("vic", role="viewer")
    assert client.post("/backup-stores", json={"name": "y", "pool": "nas"}, headers=viewer).status_code == 403
    assert client.delete("/backup-stores/main", headers=admin).status_code == 200
    assert client.get("/backup-stores/main", headers=admin).status_code == 404


def test_a_manual_backup_or_a_schedule_names_its_store(client, auth_headers, pools, monkeypatch):
    from app.routers import backups as router

    admin = auth_headers("ana")
    client.post("/backup-stores", json={"name": "main", "pool": "nas"}, headers=admin)
    ran = []
    monkeypatch.setattr(router, "run_backup", lambda name, target, **k: ran.append((name, target, k["store"])))
    monkeypatch.setattr(router, "threading", _Threads(lambda target: target()))
    monkeypatch.setattr(router, "refuse_vm_with_block_disks", lambda *a: pytest.fail("a store takes block disks"))
    r = client.post("/vms/web/backups", json={"store": "main"}, headers=admin)
    assert r.status_code == 202 and ran == [("web", None, "main")]
    r = client.post("/vms/web/backups", json={"store": "main", "target_dir": "/srv/b"}, headers=admin)
    assert r.status_code == 422
    assert client.post("/vms/web/backups", json={"store": "nope"}, headers=admin).status_code == 404
    monkeypatch.setattr(router, "_vm_exists", lambda name: True)
    r = client.put(
        "/vms/web/backup-schedule", json={"frequency": "daily", "time": "02:00", "store": "main"}, headers=admin
    )
    assert r.status_code == 200 and r.json()["store"] == "main"


def test_a_grouped_job_names_its_store(database, pools):
    from app.core import backup_groups

    backup_stores.create("main", "nas")
    job = {
        "name": "nightly",
        "selection": "all",
        "frequency": "daily",
        "time": "01:00",
        "retention_count": 7,
        "store": "main",
    }
    assert backup_groups.save_job(job)["store"] == "main"
    with pytest.raises(backup_groups.GroupError, match="not both"):
        backup_groups.validate({**job, "target_dir": "/srv/backups"})
    with pytest.raises(backup_groups.GroupError, match="No backup store"):
        backup_groups.validate({**job, "store": "nope"})


def test_files_are_not_browsed_in_a_store_backup_yet(database, pools):
    from app.core import file_restore

    backup_stores.create("main", "nas")
    store = backup_stores.open_store("main")
    backup_id = _row(_backup_into(store, "t1"), "2026-01-01T02:00:00+00:00")
    with pytest.raises(file_restore.RestoreError, match="backup store"):
        file_restore.backup_disks(backup_id)


def test_a_store_backup_records_its_directory_and_size(database, pools, monkeypatch, daemon_libvirt):
    """_run_store_backup without libvirt: the VM and its copy into the store simulated."""
    from app.core import backup_store_vm

    backup_stores.create("main", "nas")
    store = backup_stores.open_store("main")

    class Domain:
        def isActive(self):
            return True

        def XMLDesc(self, *_):
            return "<domain><name>web</name><vcpu>2</vcpu><memory>2097152</memory><devices/></domain>"

    class Conn:
        def lookupByName(self, name):
            return Domain()

        def close(self):
            pass

    monkeypatch.setattr(backups, "open_conn", lambda **_k: Conn())
    daemon_libvirt(Conn())  # the copy into the store is hyperlite-daemon's (store.backup)
    monkeypatch.setattr(
        backup_store_vm,
        "backup_vm",
        lambda domain, s, mode, progress, files, manifest_extra: _backup_into(s, "t1", b"\x02" * 9000),
    )
    backup_id = backups.run_backup("web", store="main", username="ana")
    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone())
    assert row["status"] == "succeeded" and row["store"] == "main" and row["size_bytes"] == 9000
    assert row["path"] == str(store.backup_dir(VM, "t1"))
    assert json.loads((store.backup_dir(VM, "t1") / "manifest.json").read_text())["disks"]["vda"]["size"] == 9000
