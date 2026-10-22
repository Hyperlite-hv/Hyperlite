"""Linked clones through the API (app/routers/templates.py): a deployment with `linked` gets a disk backed by the
template's disk, the template cannot be deleted or renamed while a clone reads it, its clones are listed with it, and
a linked clone made into a template gets a disk of its own. The real path (clones booting on libvirt and QEMU, and
Hyperlite's backup, disk move, resize and snapshots on them) is in docs/design/linked-clones.md."""

import shutil
import subprocess
import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import linked_disks, templates_store
from app.routers import templates
from tests.test_linked_disks import _qcow2

qemu_img = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="needs qemu-img")


class _Dom:
    def __init__(self, conn, xml):
        self.conn, self.xml = conn, xml
        self.undefined = False

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return False

    def undefineFlags(self, flags=0):
        self.undefined = True
        self.conn.doms.remove(self)


class _Conn:
    def __init__(self):
        self.doms = []

    def add(self, name, disk):
        dom = _Dom(
            self,
            f"<domain><name>{name}</name><vcpu>1</vcpu><memory>524288</memory><os><type>hvm</type></os><devices>"
            f"<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{disk}'/>"
            "<target dev='vda' bus='virtio'/></disk></devices></domain>",
        )
        self.doms.append(dom)
        return dom

    def listAllDomains(self, flags=0):
        return list(self.doms)

    def lookupByName(self, name):
        for d in self.doms:
            if d.name() == name:
                return d
        raise libvirt.libvirtError("no domain")

    def defineXML(self, xml):
        dom = _Dom(self, xml)
        self.doms.append(dom)
        return dom

    def close(self):
        pass


@pytest.fixture()
def lab(database, tmp_path, monkeypatch):
    """A templates directory and an images directory of their own, and a libvirt whose VMs this test defines."""
    tpl_dir, images = tmp_path / "templates", tmp_path / "images"
    tpl_dir.mkdir()
    images.mkdir()
    conn = _Conn()
    monkeypatch.setattr(templates_store, "TEMPLATES_DIR", tpl_dir)
    monkeypatch.setattr(templates, "IMAGES_DIR", images)
    monkeypatch.setattr(templates, "open_conn", lambda *a: conn)
    monkeypatch.setattr(templates, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(templates.maintenance, "refuse_if_in_maintenance", lambda *a: None)

    def template(name, real=False):
        templates_store.save_template(name, conn_xml(name), 1, 512, "src", "root")
        disk = templates_store.disk_path(name)
        if real:
            subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(disk), "64M"], check=True)
        else:
            _qcow2(disk)
        return disk

    def conn_xml(name):
        return (
            f"<domain><name>{name}</name><os><type>hvm</type></os><devices><disk type='file' device='disk'>"
            "<driver name='qemu' type='raw'/><source file='/x.img'/><target dev='vda' bus='virtio'/></disk>"
            "</devices></domain>"
        )

    return {"conn": conn, "images": images, "tpl_dir": tpl_dir, "template": template}


@qemu_img
def test_a_linked_deployment_reads_through_the_template(lab, client, auth_headers):
    tpl = lab["template"]("base", real=True)
    r = client.post("/templates/base/deploy", json={"new_name": "web-1", "linked": True}, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    assert r.json()["linked"] is True
    disk = lab["images"] / "web-1.qcow2"
    assert linked_disks.chain(disk) == [str(disk), str(tpl)]
    root = ET.fromstring(lab["conn"].doms[-1].xml)
    assert root.find("devices/disk/source").get("file") == str(disk)
    assert root.find("devices/disk/driver").get("type") == "qcow2", "the new disk is qcow2, whatever the source had"


@qemu_img
def test_a_full_deployment_stays_the_default(lab, client, auth_headers):
    lab["template"]("base", real=True)
    r = client.post("/templates/base/deploy", json={"new_name": "web-1"}, headers=auth_headers("root"))
    assert r.status_code == 201 and r.json()["linked"] is False
    assert linked_disks.backing_file(lab["images"] / "web-1.qcow2") is None


def test_a_template_whose_disk_is_not_qcow2_is_only_copied(lab, client, auth_headers):
    lab["template"]("base")
    templates_store.disk_path("base").write_bytes(b"\0" * 4096)
    admin = auth_headers("root")
    assert [t["linkable"] for t in client.get("/templates", headers=admin).json()["items"]] == [False]
    r = client.post("/templates/base/deploy", json={"new_name": "web-1", "linked": True}, headers=admin)
    assert r.status_code == 422 and "full copy" in r.json()["detail"]
    assert list(lab["images"].iterdir()) == []


def test_a_template_with_linked_clones_is_neither_deleted_nor_renamed(lab, client, auth_headers):
    tpl = lab["template"]("base")
    lab["template"]("other")
    lab["conn"].add("web-2", _qcow2(lab["images"] / "web-2.qcow2", str(tpl)))
    lab["conn"].add("web-1", _qcow2(lab["images"] / "web-1.qcow2", str(tpl)))
    admin = auth_headers("root")

    gone = client.delete("/templates/base?confirm=true", headers=admin)
    assert gone.status_code == 409 and "web-1, web-2" in gone.json()["detail"]
    moved = client.post("/templates/base/rename", json={"new_name": "renamed"}, headers=admin)
    assert moved.status_code == 409 and "rename it" in moved.json()["detail"]
    assert tpl.exists() and templates_store.exists("base")

    items = client.get("/templates", headers=admin).json()["items"]
    assert {t["name"]: t["linked_clones"] for t in items} == {"base": ["web-1", "web-2"], "other": []}
    assert all(t["linkable"] for t in items)

    for vm in list(lab["conn"].doms):
        vm.undefineFlags()
    assert client.post("/templates/base/rename", json={"new_name": "renamed"}, headers=admin).status_code == 200
    assert client.delete("/templates/renamed?confirm=true", headers=admin).status_code == 200


def test_without_libvirt_the_list_still_answers_and_nothing_is_removed(lab, client, auth_headers, monkeypatch):
    lab["template"]("base")

    def down(*a):
        raise libvirt.libvirtError("Failed to connect socket")

    monkeypatch.setattr(templates, "open_conn", down)
    admin = auth_headers("root")
    assert client.get("/templates", headers=admin).json()["items"][0]["linked_clones"] is None
    r = client.delete("/templates/base?confirm=true", headers=admin)
    assert r.status_code == 503 and "cannot be checked" in r.json()["detail"]
    assert templates_store.exists("base")


@qemu_img
def test_a_linked_clone_made_into_a_template_gets_a_disk_of_its_own(lab, client, auth_headers):
    tpl = lab["template"]("base", real=True)
    clone = lab["images"] / "web-1.qcow2"
    linked_disks.create_linked(tpl, clone)
    vm = lab["conn"].add("web-1", clone)
    r = client.post("/templates/from-vm/web-1", json={"template_name": "web-tpl"}, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    new = templates_store.disk_path("web-tpl")
    assert new.exists() and linked_disks.backing_file(new) is None, "flattened: it depends on no other template"
    assert not clone.exists() and vm.undefined
    assert not list(lab["tpl_dir"].glob(".*.part")), "no copy left aside"
    gone = client.delete("/templates/base?confirm=true", headers=auth_headers("eve"))
    assert gone.status_code == 200, "the base template has no clone left"
