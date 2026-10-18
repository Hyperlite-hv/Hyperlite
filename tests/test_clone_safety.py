"""Cloning a VM (code review, #448): the clone of a raw disk is a qcow2 file and says so; a copy that fails, or any
other error, leaves no disk file behind and closes the task; two clones never write the same new disk files."""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
import pytest

from app.core import audit, vm_locks
from app.routers.vms import clone

RAW_VM = """<domain><name>old</name><uuid>1</uuid><devices>
<disk type='file' device='disk'><driver name='qemu' type='raw'/><source file='{src}'/><target dev='vda'/></disk>
<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default'/></interface>
</devices></domain>"""


class FakeDomain:
    def __init__(self, xml):
        self.xml = xml

    def isActive(self):
        return False

    def XMLDesc(self, flags=0):
        return self.xml

    def name(self):
        return ET.fromstring(self.xml).findtext("name")


class FakeConn:
    def __init__(self, src):
        self.domains = {"old": FakeDomain(RAW_VM.format(src=src))}
        self.defined = []

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("Domain not found")
        return self.domains[name]

    def defineXML(self, xml):
        self.defined.append(xml)
        return FakeDomain(xml)

    def close(self):
        pass


@pytest.fixture()
def env(database, tmp_path, monkeypatch):
    src = tmp_path / "old.img"
    src.write_bytes(b"raw disk")
    images = tmp_path / "images"
    images.mkdir()
    conn = FakeConn(src)
    monkeypatch.setattr(clone, "open_conn", lambda node=None: conn)
    monkeypatch.setattr(clone, "IMAGES_DIR", images)
    monkeypatch.setattr(clone, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(clone, "generate_mac", lambda conn: "52:54:00:00:00:02")
    monkeypatch.setattr(clone, "allocate_static_ip", lambda *a: None)

    def copy(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"QFI\xfb")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(clone.subprocess, "run", copy)

    def task():
        audit._AUDIT_QUEUE.join()
        with database.get_conn() as db:
            return dict(db.execute("SELECT status, error FROM tasks WHERE type = 'clone_vm'").fetchone())

    return {"conn": conn, "images": images, "task": task}


def test_the_clone_of_a_raw_disk_is_defined_as_qcow2(env, client, auth_headers):
    r = client.post("/vms/old/clone", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    disk = ET.fromstring(env["conn"].defined[0]).find(".//devices/disk")
    assert disk.find("driver").get("type") == "qcow2"
    assert disk.find("source").get("file") == str(env["images"] / "copy.qcow2")


def test_a_copy_that_stops_leaves_no_partial_file(env, client, auth_headers, monkeypatch):
    def full(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"half")
        raise subprocess.CalledProcessError(1, cmd, "", "No space left on device")

    monkeypatch.setattr(clone.subprocess, "run", full)
    r = client.post("/vms/old/clone", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 500
    assert list(env["images"].iterdir()) == []
    assert env["task"]()["status"] == "failed"


def test_an_unexpected_error_removes_the_copies_and_closes_the_task(env, client, auth_headers, monkeypatch):
    def broken(conn):
        raise OSError("entropy source unavailable")

    monkeypatch.setattr(clone, "generate_mac", broken)
    with pytest.raises(OSError):
        client.post("/vms/old/clone", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert list(env["images"].iterdir()) == []
    task = env["task"]()
    assert task["status"] == "failed" and "entropy" in task["error"]


def test_two_clones_never_write_the_same_new_disks(env, client, auth_headers):
    with vm_locks.claim("copy", "a clone"):
        r = client.post("/vms/old/clone", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 409 and env["conn"].defined == []


def test_a_clone_that_would_not_fit_is_refused_before_any_copy(env, client, auth_headers, monkeypatch):
    monkeypatch.setattr(clone.disk_space, "allocated_bytes", lambda path: 50 * 1024**3)
    monkeypatch.setattr(clone.disk_space.shutil, "disk_usage", lambda path: type("U", (), {"free": 20 * 1024**3})())
    r = client.post("/vms/old/clone", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 409 and "Not enough space for this clone" in r.json()["detail"]
    assert list(env["images"].iterdir()) == [] and env["conn"].defined == []
    assert env["task"]()["status"] == "failed"
