"""After a successful live migration the VM runs on its new node whatever happens next (#448, finding F11). The steps
that follow (moving its HA protection, tags, start at boot, cleaning the source) used to run in one block: the first
one to fail marked the task failed and skipped the others, HA protection included, which left HA watching the node
the VM had left and could later start a second copy of it there."""

import pytest

from app.core import audit, checkpoints, ha, network_alloc, object_meta, snapshot_carry, vm_boot, vm_migrate
from app.core import cluster as cluster_mod
from app.core.tasks import create_task
from app.routers.vms import migration


class FakeDomain:
    def isActive(self):
        return True

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
def migrate(database, monkeypatch, daemon_libvirt):
    daemon_libvirt(FakeConn())  # both nodes' libvirt, as hyperlite-daemon opens them with the cluster's key
    monkeypatch.setattr(vm_migrate, "_ensure_networks_active", lambda conn, names: None)
    monkeypatch.setattr(vm_migrate, "domain_disk_paths", lambda domain: [])
    monkeypatch.setattr(vm_migrate, "uses_shared_storage", lambda *a: True)
    monkeypatch.setattr(network_alloc, "carry_reservations", lambda *a: ([], []))
    monkeypatch.setattr(network_alloc, "release_reservations", lambda *a: None)
    monkeypatch.setattr(checkpoints, "drop", lambda *a: 0)
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


def test_the_default_pool_is_made_on_the_destination_before_the_disk_is_copied(migrate, monkeypatch):
    """A fresh node defines the 'default' pool only when something first asks for it, and libvirt creates the copied
    disk through the pool of its directory: the migration failed with "no storage pool with matching target path"."""
    order = []
    monkeypatch.setattr(vm_migrate, "domain_disk_paths", lambda domain: ["/var/lib/libvirt/images/web.qcow2"])
    monkeypatch.setattr(vm_migrate, "uses_shared_storage", lambda *a: False)
    monkeypatch.setattr(vm_migrate, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(vm_migrate, "delete_paths_on_node", lambda *a: None)
    monkeypatch.setattr(vm_migrate, "ensure_default_pool", lambda conn: order.append("pool"))
    monkeypatch.setattr(FakeDomain, "migrateToURI3", lambda self, uri, params, flags: order.append("migrate"))
    task, _ = migrate["run"]()
    assert task["status"] == "succeeded", task
    assert order[:2] == ["pool", "migrate"]


def test_no_pool_is_made_for_a_disk_elsewhere(monkeypatch):
    made = []
    monkeypatch.setattr(vm_migrate, "ensure_default_pool", lambda conn: made.append(conn))
    vm_migrate._ensure_default_pool_for("dest", ["/mnt/pve/nfs/web.qcow2", "/var/lib/libvirt/images/sub/x.qcow2"])
    assert made == []
    vm_migrate._ensure_default_pool_for("dest", ["/var/lib/libvirt/images/web.qcow2"])
    assert made == ["dest"]
