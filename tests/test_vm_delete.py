"""A deleted VM takes its own disks only, as on Proxmox VE (app/core/vm_delete.py): a disk another VM uses or reads
through, or a file no storage pool holds, stays, and the answer says so. vSphere's "Delete from disk" took a disk
shared with another VM along."""

import libvirt
import pytest

from app.core import privileged, vm_delete
from app.daemon.registry import DaemonError
from app.routers.vms import lifecycle


class _Dom:
    def __init__(self, name, *disks, running=False):
        self._name, self.disks, self.running, self.undefined = name, disks, running, False

    def name(self):
        return self._name

    def isActive(self):
        return self.running

    def XMLDesc(self, flags=0):
        body = "".join(
            f"<disk type='file' device='disk'><source file='{d}'/><target dev='vd{c}'/></disk>"
            for d, c in zip(self.disks, "abcdef", strict=False)
        )
        return f"<domain><name>{self._name}</name><devices>{body}</devices></domain>"

    def undefineFlags(self, flags):
        self.undefined = True


class _Conn:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def listAllDomains(self, flags=0):
        return list(self.domains.values())

    def listAllStoragePools(self, flags=0):
        return []

    def storageVolLookupByPath(self, path):
        raise libvirt.libvirtError("no storage vol with matching path")


@pytest.fixture()
def images(tmp_path, monkeypatch):
    monkeypatch.setattr(vm_delete, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(vm_delete, "refresh_pools_for_paths", lambda conn, paths: None)
    for name in ("web.qcow2", "web-2.qcow2", "shared.qcow2", "base.qcow2"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_a_disk_another_vm_uses_stays(images):
    web = _Dom("web", images / "web.qcow2", images / "shared.qcow2")
    conn = _Conn(web, _Dom("db", images / "db.qcow2", images / "shared.qcow2"))
    keep = vm_delete.delete(conn, "web")
    assert web.undefined and not (images / "web.qcow2").exists()
    assert (images / "shared.qcow2").exists() and keep == {str(images / "shared.qcow2"): "used by VM db"}


def test_the_base_of_another_vms_linked_disk_stays(images, monkeypatch):
    monkeypatch.setattr(
        vm_delete.linked_disks,
        "depends_on",
        lambda path, base: (path, base) == (str(images / "db.qcow2"), str(images / "base.qcow2")),
    )
    conn = _Conn(_Dom("web", images / "base.qcow2", images / "web-2.qcow2"), _Dom("db", images / "db.qcow2"))
    keep = vm_delete.delete(conn, "web")
    assert (images / "base.qcow2").exists() and not (images / "web-2.qcow2").exists()
    assert keep == {str(images / "base.qcow2"): "base of the linked disks of VM db"}


def test_a_file_no_storage_pool_holds_stays(images, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "data.img"
    outside.write_bytes(b"attached by hand")
    keep = vm_delete.delete(_Conn(_Dom("web", images / "web.qcow2", outside)), "web")
    assert outside.exists() and keep == {str(outside): "in no storage pool"}
    assert not (images / "web.qcow2").exists()


def test_the_daemon_checks_the_vm_again(daemon_libvirt, images):
    daemon_libvirt(_Conn(_Dom("web", images / "web.qcow2", running=True)))
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.delete", vm="web")
    assert e.value.kind == "in_use" and (images / "web.qcow2").exists()
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.delete", vm="gone")
    assert e.value.kind == "not_found"
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.delete", vm="x" * 65)
    assert e.value.kind == "invalid_arguments"


def test_the_answer_and_the_audit_say_what_stays(client, auth_headers, daemon_libvirt, images, monkeypatch):
    web = _Dom("web", images / "web.qcow2", images / "shared.qcow2")
    conn = daemon_libvirt(_Conn(web, _Dom("db", images / "shared.qcow2")))
    monkeypatch.setattr(lifecycle, "open_conn", lambda node=None: _Kept(conn))
    logged = []
    monkeypatch.setattr(lifecycle, "log_action", lambda *a, **k: logged.append(a))
    r = client.delete("/vms/web?confirm=true", headers=auth_headers("root"))
    assert r.status_code == 200
    assert r.json()["kept_disks"] == {str(images / "shared.qcow2"): "used by VM db"}
    assert logged[-1][3:] == ("success", f"disk {images / 'shared.qcow2'} kept (used by VM db)")


class _Kept:
    """The API's own connection: the route closes it."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        pass
