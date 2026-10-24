"""A template made from a VM: converted (the VM becomes the template and leaves the list, as Proxmox VE's "Convert to
template") or copied (the VM stays, as vSphere's "Clone to Template"). Seen on a real node: an administrator converted a
VM expecting a copy, and the VM was gone from the list."""

import shutil
import subprocess
from unittest.mock import Mock

import libvirt
import pytest

from app.core import disk_space, templates_store
from app.routers import templates


class _Domain:
    def __init__(self, disk, active=False):
        self.disk = disk
        self.active = active
        self.undefined = False

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        return (
            "<domain type='kvm'><name>web</name><vcpu>2</vcpu><memory>2097152</memory><os><type>hvm</type></os>"
            f"<devices><disk type='file' device='disk'><source file='{self.disk}'/></disk></devices></domain>"
        )

    def undefineFlags(self, flags):
        self.undefined = True


class _Conn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def close(self):
        pass


@pytest.fixture()
def node(database, tmp_path, monkeypatch):
    images, tpl_dir = tmp_path / "images", tmp_path / "templates"
    images.mkdir()
    tpl_dir.mkdir()
    disk = images / "web.qcow2"
    disk.write_bytes(b"the VM's disk")
    domain = _Domain(disk)
    copies = []

    def qemu_img(cmd, **k):
        assert cmd[:4] == ["qemu-img", "convert", "-O", "qcow2"]
        copies.append(cmd)
        shutil.copyfile(cmd[4], cmd[5])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(templates_store, "TEMPLATES_DIR", tpl_dir)
    monkeypatch.setattr(templates, "IMAGES_DIR", images)
    monkeypatch.setattr(templates, "open_conn", lambda *a: _Conn(domain))
    monkeypatch.setattr(templates, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(templates.subprocess, "run", qemu_img)
    monkeypatch.setattr(disk_space, "ensure_room", lambda *a: None)
    monkeypatch.setattr(disk_space, "allocated_bytes", lambda path: 13)
    return {"disk": disk, "domain": domain, "tpl_dir": tpl_dir, "copies": copies}


def _make(client, headers, **body):
    return client.post("/templates/from-vm/web", json={"template_name": "base", **body}, headers=headers)


def test_a_copy_keeps_the_vm_and_its_disk(node, client, auth_headers):
    r = _make(client, auth_headers("root"), keep_vm=True)
    assert r.status_code == 201, r.text
    assert r.json() == {"template": "base", "vm_source": "web", "vm_kept": True}
    assert not node["domain"].undefined
    assert node["disk"].read_bytes() == b"the VM's disk"  # still the VM's
    assert (node["tpl_dir"] / "base.qcow2").read_bytes() == b"the VM's disk"
    assert [t["name"] for t in templates_store.list_templates()] == ["base"]
    assert not list(node["tpl_dir"].glob(".*.part"))


def test_a_conversion_still_consumes_the_vm(node, client, auth_headers):
    r = _make(client, auth_headers("root"))
    assert r.status_code == 201, r.text
    assert r.json()["vm_kept"] is False
    assert node["domain"].undefined and not node["disk"].exists()
    assert (node["tpl_dir"] / "base.qcow2").read_bytes() == b"the VM's disk"
    assert node["copies"] == []  # moved, not copied


def test_a_copy_that_fails_leaves_no_template_and_the_vm_untouched(node, client, auth_headers, monkeypatch):
    def broken(cmd, **k):
        (node["tpl_dir"] / ".base.qcow2.part").write_bytes(b"half")
        raise subprocess.CalledProcessError(1, cmd, "", "No space left on device")

    monkeypatch.setattr(templates.subprocess, "run", broken)
    r = _make(client, auth_headers("root"), keep_vm=True)
    assert r.status_code == 500
    assert templates_store.list_templates() == [] and list(node["tpl_dir"].iterdir()) == []
    assert not node["domain"].undefined and node["disk"].exists()


def test_a_copy_that_would_not_fit_is_refused_before_it_starts(node, client, auth_headers, monkeypatch):
    def full(*a):
        raise disk_space.NotEnoughSpace("Not enough space for this template's disk")

    monkeypatch.setattr(disk_space, "ensure_room", full)
    r = _make(client, auth_headers("root"), keep_vm=True)
    assert r.status_code == 409 and "Not enough space" in r.json()["detail"]
    assert node["copies"] == [] and templates_store.list_templates() == []


@pytest.mark.parametrize("keep_vm", [True, False])
def test_a_running_vm_is_refused_in_both_modes(node, client, auth_headers, keep_vm):
    node["domain"].active = True
    assert _make(client, auth_headers("root"), keep_vm=keep_vm).status_code == 409
    assert not node["domain"].undefined and node["copies"] == []


def test_the_vms_account_and_system_go_with_its_template(node, client, auth_headers):
    """Seen on a real node: an ISO-installed RHEL converted to a template, then deployed, had no SSH user, so the web
    terminal could not reach it; the source's rows stayed under a VM that no longer existed."""
    from app.core import vm_meta

    vm_meta.set_vm_ssh_user("web", "ansible")
    vm_meta.set_vm_os_label("web", "rhel-10.2-x86_64-dvd")
    admin = auth_headers("root")
    assert _make(client, admin, keep_vm=True).status_code == 201
    copied = templates_store.get_template("base")
    assert (copied["ssh_user"], copied["os_label"]) == ("ansible", "rhel-10.2-x86_64-dvd")
    assert vm_meta.get_vm_ssh_user("web") == "ansible"  # a copy: the VM keeps them

    r = client.post("/templates/from-vm/web", json={"template_name": "conv"}, headers=admin)
    assert r.status_code == 201, r.text
    converted = templates_store.get_template("conv")
    assert (converted["ssh_user"], converted["os_label"]) == ("ansible", "rhel-10.2-x86_64-dvd")
    assert vm_meta.get_vm_ssh_user("web") is None and vm_meta.get_vm_os_label("web") is None  # the VM is gone


def test_a_vm_without_account_or_system_makes_a_template_without_them(node, client, auth_headers):
    assert _make(client, auth_headers("root"), keep_vm=True).status_code == 201
    tpl = templates_store.get_template("base")
    assert "ssh_user" not in tpl and "os_label" not in tpl


class _Network:
    def __init__(self):
        self.updates = []

    def XMLDesc(self, *a):
        return "<network><ip><dhcp><host mac='52:54:00:aa:bb:cc' ip='192.168.122.50'/></dhcp></ip></network>"

    def update(self, *args):
        self.updates.append(args[3])


def _with_interface(node, monkeypatch):
    network = _Network()
    xml = (
        node["domain"]
        .XMLDesc(0)
        .replace(
            "</devices>",
            "<interface type='network'><mac address='52:54:00:aa:bb:cc'/><source network='default'/></interface></devices>",
        )
    )
    monkeypatch.setattr(node["domain"], "XMLDesc", lambda flags: xml)
    monkeypatch.setattr(_Conn, "networkLookupByName", lambda self, name: network, raising=False)
    return network


def _records_of_web():
    from app.core import vm_meta
    from app.services import backup_service

    vm_meta.set_vm_ssh_user("web", "ansible")
    vm_meta.set_vm_auto_cleanup("web", 30)
    backup_service.set_schedule("web", "daily", "02:00", "/srv/b", 7, None, None, None, "2030-01-01T02:00:00")


def test_a_conversion_forgets_the_vm_as_a_deletion_does(node, client, auth_headers, monkeypatch):
    """The VM is gone: its backup schedule failed at every run, its rights, pools, automatic deletion and DHCP
    reservation stayed under its name."""
    from app.core import vm_meta
    from app.services import backup_service

    network = _with_interface(node, monkeypatch)
    _records_of_web()
    r = client.post("/templates/from-vm/web", json={"template_name": "conv"}, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    assert backup_service.schedule_of("web") is None
    assert vm_meta.get_vm_auto_cleanup("web") is None and vm_meta.get_vm_ssh_user("web") is None
    assert network.updates == ["<host mac='52:54:00:aa:bb:cc' ip='192.168.122.50'/>"]
    assert templates_store.get_template("conv")["ssh_user"] == "ansible"  # the template keeps what a deployment needs


def test_a_copy_forgets_nothing(node, client, auth_headers, monkeypatch):
    from app.core import vm_meta
    from app.services import backup_service

    network = _with_interface(node, monkeypatch)
    _records_of_web()
    assert _make(client, auth_headers("root"), keep_vm=True).status_code == 201
    assert backup_service.schedule_of("web") is not None and vm_meta.get_vm_auto_cleanup("web") is not None
    assert network.updates == []


def test_a_conversion_and_a_deletion_forget_through_the_same_list(
    node, client, auth_headers, monkeypatch, daemon_libvirt
):
    from app.core import vm_records
    from app.routers.vms import lifecycle

    forgotten = []
    monkeypatch.setattr(vm_records, "forget", lambda name, node=None: forgotten.append(name))
    assert (
        client.post("/templates/from-vm/web", json={"template_name": "conv"}, headers=auth_headers("root")).status_code
        == 201
    )
    domain = _Domain("/nonexistent/db.qcow2")
    conn = daemon_libvirt(Mock())  # no other VM, no pool
    conn.lookupByName.return_value = domain
    conn.listAllDomains.return_value = []
    conn.listAllStoragePools.return_value = []
    conn.storageVolLookupByPath.side_effect = libvirt.libvirtError("no storage vol with matching path")
    lifecycle._perform_vm_deletion(conn, domain, "db")
    assert forgotten == ["web", "db"]
