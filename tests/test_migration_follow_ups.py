"""After a successful live migration the VM runs on its new node whatever happens next (#448, finding F11). The steps
that follow (moving its HA protection, tags, start at boot, cleaning the source) used to run in one block: the first
one to fail marked the task failed and skipped the others, HA protection included, which left HA watching the node
the VM had left and could later start a second copy of it there."""

import pytest

from app.core import audit, checkpoints, ha, network_alloc, object_meta, snapshot_carry, vm_boot
from app.core import cluster as cluster_mod
from app.core.tasks import create_task
from app.routers.vms import migration


class FakeDomain:
    def XMLDesc(self, flags=0):
        return "<domain><name>web</name><devices/></domain>"

    def migrateToURI3(self, uri, params, flags):
        pass

    def jobInfo(self):
        return [0, 0, 0, 0, 0, 0]


class FakeConn:
    def lookupByName(self, name):
        return FakeDomain()

    def close(self):
        pass


@pytest.fixture()
def migrate(database, monkeypatch):
    monkeypatch.setattr(migration, "open_conn", lambda node=None: FakeConn())
    monkeypatch.setattr(migration, "_ensure_networks_active", lambda conn, names: None)
    monkeypatch.setattr(migration, "domain_disk_paths", lambda domain: [])
    monkeypatch.setattr(migration, "uses_shared_storage", lambda *a: True)
    monkeypatch.setattr(network_alloc, "carry_reservations", lambda *a: ([], []))
    monkeypatch.setattr(network_alloc, "release_reservations", lambda *a: None)
    monkeypatch.setattr(checkpoints, "release", lambda *a: None)
    monkeypatch.setattr(snapshot_carry, "stash", lambda domain: None)
    monkeypatch.setattr(cluster_mod, "get_node", lambda key: {"hostname": "10.0.0.2"})
    monkeypatch.setattr(cluster_mod, "build_libvirt_uri", lambda node: "qemu+ssh://root@10.0.0.2/system")
    calls = []
    monkeypatch.setattr(object_meta, "follow_migration", lambda *a: calls.append("meta"))
    monkeypatch.setattr(vm_boot, "follow_migration", lambda *a: calls.append("boot") or True)

    def run():
        task_id = create_task("migrate_vm", "web", "n1", "admin")
        migration._migrate_vm_job(task_id, "admin", "n1", "n2", "web")
        audit._AUDIT_QUEUE.join()
        with database.get_conn() as db:
            task = dict(db.execute("SELECT status, error FROM tasks WHERE id = ?", (task_id,)).fetchone())
            entries = {r["action"]: (r["result"], r["error_message"]) for r in db.execute("SELECT * FROM audit_log")}
        return task, entries

    return {"run": run, "calls": calls}


def test_a_failed_step_after_the_migration_skips_nothing_and_fails_nothing(migrate, monkeypatch):
    def broken(*a):
        raise OSError("database is locked")

    monkeypatch.setattr(ha, "follow_migration", broken)
    task, audit = migrate["run"]()
    assert task["status"] == "succeeded"  # the VM did move
    assert "moving its HA protection" in task["error"]  # shown as a warning on the task
    assert migrate["calls"] == ["meta", "boot"]  # the steps after it still ran
    assert audit["ha_follow"][0] == "failed" and "still names n1" in audit["ha_follow"][1]
    assert audit["migrate_vm"][0] == "success" and "with warnings" in audit["migrate_vm"][1]


def test_the_ha_protection_moves_before_anything_that_can_fail(migrate, monkeypatch):
    order = []
    monkeypatch.setattr(ha, "follow_migration", lambda *a: order.append("ha") or True)
    monkeypatch.setattr(object_meta, "follow_migration", lambda *a: order.append("meta"))

    def broken(*a):
        order.append("release")
        raise OSError("unreachable")

    monkeypatch.setattr(network_alloc, "release_reservations", broken)
    task, audit = migrate["run"]()
    assert order[:2] == ["ha", "meta"] and "release" in order
    assert task["status"] == "succeeded" and "releasing its addresses" in task["error"]
    assert audit["ha_follow"][0] == "success"


def test_a_clean_migration_has_no_warning(migrate, monkeypatch):
    monkeypatch.setattr(ha, "follow_migration", lambda *a: False)
    task, audit = migrate["run"]()
    assert task == {"status": "succeeded", "error": None}
    assert "with warnings" not in audit["migrate_vm"][1]
