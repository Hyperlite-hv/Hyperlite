"""Moving a disk between pools: the endpoint checks before starting (admin only, clear refusals), runs the move as
a task, and records success or failure. The copy itself (blockCopy + pivot, qemu-img convert) was checked against a
real libvirt/QEMU; here the libvirt side is replaced by fakes."""

import libvirt
import pytest

from app.core import disk_move
from app.routers.vms import devices

POOLS = {
    "default": ("dir", "/var/lib/libvirt/images"),
    "nfs1": ("netfs", "/mnt/nfs1"),
    "tank": ("zfs", None),
}


class FakePool:
    def __init__(self, name):
        self._name = name
        self.refreshed = 0

    def name(self):
        return self._name

    def XMLDesc(self, *_):
        kind, target = POOLS[self._name]
        path = f"<target><path>{target}</path></target>" if target else ""
        return f"<pool type='{kind}'><name>{self._name}</name>{path}</pool>"

    def isActive(self):
        return True

    def refresh(self, flags):
        self.refreshed += 1


class FakeVol:
    def storagePoolLookupByVolume(self):
        return FakePool("default")


class FakeDomain:
    def listAllCheckpoints(self):
        return []  # no replication checkpoint (app/core/checkpoints.py)

    def __init__(self, snapshots=0, disk_type="file", fmt="qcow2", device="disk"):
        self.snapshots = snapshots
        source = (
            "<source file='/var/lib/libvirt/images/vm1.qcow2'/>" if disk_type == "file" else "<source dev='/dev/sdz'/>"
        )
        self._xml = (
            f"<domain><name>vm1</name><devices><disk type='{disk_type}' device='{device}'>"
            f"<driver name='qemu' type='{fmt}'/>{source}<target dev='vda' bus='virtio'/></disk></devices></domain>"
        )

    def name(self):
        return "vm1"

    def XMLDesc(self, *_):
        return self._xml

    def isActive(self):
        return False

    def snapshotNum(self, flags):
        return self.snapshots

    def blockInfo(self, dev):
        return (10 * 1024**3, 0, 0)


class FakeConn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        if name != "vm1":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def storageVolLookupByPath(self, path):
        return FakeVol()

    def listAllStoragePools(self, flags):
        return [FakePool(name) for name in POOLS]

    def close(self):
        pass


def test_the_plan_targets_the_destination_pool_directory():
    how = disk_move.plan(FakeConn(FakeDomain()), FakeDomain(), "vda", "nfs1")
    assert how["dest"] == "/mnt/nfs1/vm1.qcow2" and how["live"] is False and how["capacity"] == 10 * 1024**3


@pytest.mark.parametrize(
    ("domain", "pool", "status", "text"),
    [
        (FakeDomain(), "default", 422, "already in pool"),
        (FakeDomain(), "tank", 422, "directory and NFS"),
        (FakeDomain(), "ghost", 404, "not found"),
        (FakeDomain(snapshots=2), "nfs1", 409, "snapshots"),
        (FakeDomain(disk_type="block"), "nfs1", 422, "block device"),
        (FakeDomain(fmt="vmdk"), "nfs1", 422, "qcow2 and raw"),
        (FakeDomain(device="cdrom"), "nfs1", 422, "Only hard disks"),
    ],
)
def test_refusals_are_clear(domain, pool, status, text):
    with pytest.raises(disk_move.MoveError) as err:
        disk_move.plan(FakeConn(domain), domain, "vda", pool)
    assert err.value.status == status and text in err.value.message


@pytest.fixture()
def api(client, auth_headers, monkeypatch):
    domain = FakeDomain()
    monkeypatch.setattr(devices, "open_conn", lambda *a, **k: FakeConn(domain))
    monkeypatch.setattr(devices, "_run_in_background", lambda target, *args: target(*args))
    return {"client": client, "headers": auth_headers("admin1")}


def _task(database, type_="move_disk"):
    with database.get_conn() as db:
        return dict(db.execute("SELECT * FROM tasks WHERE type = ?", (type_,)).fetchone())


def test_the_endpoint_runs_the_move_as_a_task(api, database, monkeypatch):
    calls = []
    monkeypatch.setattr(
        disk_move,
        "move",
        lambda domain, dev, how, delete, progress, stop=None: (
            calls.append((dev, how["dest"], delete))
            or {"source": how["source"], "destination": how["dest"], "source_supprimee": delete}
        ),
    )
    r = api["client"].post(
        "/vms/vm1/disks/vda/move", json={"pool": "nfs1", "delete_source": True}, headers=api["headers"]
    )
    assert r.status_code == 202, r.text
    assert r.json()["destination"] == "/mnt/nfs1/vm1.qcow2" and r.json()["a_chaud"] is False
    assert calls == [("vda", "/mnt/nfs1/vm1.qcow2", True)]
    assert _task(database)["statut"] == "termine"


def test_a_failed_move_fails_its_task_with_the_reason(api, database, monkeypatch):
    def boom(*args, **kwargs):
        raise disk_move.MoveError("qemu-img convert failed: no space left on device", 500)

    monkeypatch.setattr(disk_move, "move", boom)
    assert (
        api["client"].post("/vms/vm1/disks/vda/move", json={"pool": "nfs1"}, headers=api["headers"]).status_code == 202
    )
    task = _task(database)
    assert task["statut"] == "echec" and "no space left" in task["erreur"]


def test_refusals_come_back_before_any_task(api, database):
    r = api["client"].post("/vms/vm1/disks/vda/move", json={"pool": "default"}, headers=api["headers"])
    assert r.status_code == 422 and "already in pool" in r.json()["detail"]
    assert (
        api["client"].post("/vms/nope/disks/vda/move", json={"pool": "nfs1"}, headers=api["headers"]).status_code == 404
    )
    with database.get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE type = 'move_disk'").fetchone()[0] == 0


def test_only_an_admin_can_move_a_disk(api, auth_headers):
    headers = auth_headers("watcher", role="observateur")
    assert api["client"].post("/vms/vm1/disks/vda/move", json={"pool": "nfs1"}, headers=headers).status_code == 403
