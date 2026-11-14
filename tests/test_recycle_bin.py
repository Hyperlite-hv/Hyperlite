"""The recycle bin of deleted VMs (app/core/recycle_bin.py), with hyperlite-daemon's operations stood in for."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import privileged, recycle_bin


class Domain:
    def __init__(self, name="web-1"):
        self._name = name

    def name(self):
        return self._name

    def info(self):
        return [5, 2048 * 1024, 0, 2, 0]


@pytest.fixture()
def daemon(monkeypatch):
    calls = []

    def run(op, user=None, timeout=None, **args):
        calls.append((op, args["vm"]))
        return {"vm.bin": {"disks": ["/var/lib/hyperlite/images/web-1.qcow2"]}, "vm.unbin": {"restored": args["vm"]},
                "vm.bin_purge": {"kept": {}}}[op]  # fmt: skip

    monkeypatch.setattr(privileged, "run", run)
    return calls


def test_bin_restore_purge(database, daemon):
    assert recycle_bin.days() == 7
    row = recycle_bin.put(Domain(), "root")
    assert row["disks"] == ["/var/lib/hyperlite/images/web-1.qcow2"] and row["vcpu"] == 2 and row["memory_mb"] == 2048
    assert recycle_bin.holds("web-1")
    recycle_bin.restore("web-1", "root")
    assert not recycle_bin.holds("web-1")
    recycle_bin.put(Domain(), "root")
    recycle_bin.purge_due(datetime.now(UTC) + timedelta(days=6))
    assert recycle_bin.holds("web-1")  # not yet
    recycle_bin.purge_due(datetime.now(UTC) + timedelta(days=8))
    assert not recycle_bin.holds("web-1")
    assert [op for op, _ in daemon] == ["vm.bin", "vm.unbin", "vm.bin", "vm.bin_purge"]


def test_routes(client, auth_headers, daemon):
    admin = auth_headers("root", "admin")
    recycle_bin.put(Domain("db-1"), "root")
    listed = client.get("/recycle-bin", headers=admin).json()
    assert listed["days"] == 7 and [i["vm_name"] for i in listed["items"]] == ["db-1"]
    assert client.get("/recycle-bin", headers=auth_headers("bob", "viewer")).json()["items"] == []
    assert client.post("/recycle-bin/db-1/restore", headers=auth_headers("eve", "viewer")).status_code == 403
    assert client.post("/recycle-bin/nope/restore", headers=admin).status_code == 404
    assert client.post("/recycle-bin/db-1/restore", headers=admin).status_code == 200
    assert client.put("/recycle-bin/settings", json={"days": 0}, headers=admin).json()["days"] == 0
    assert recycle_bin.days() == 0


def test_the_name_stays_taken(database, daemon):
    import libvirt

    from app.core import vm_rename

    recycle_bin.put(Domain("kept"), "root")

    class Conn:
        def lookupByName(self, name):
            raise libvirt.libvirtError("no such domain")

    with pytest.raises(vm_rename.RenameError, match="recycle bin"):
        vm_rename.rename(Conn(), None, "other", "kept")
