"""A new VM's disks are new files (GHSA-vfmj-3v3j-48fq): VM "web-2" would otherwise take "web"'s second disk,
web-2.qcow2, as its own system disk and write over it."""

import importlib
from unittest.mock import Mock

import libvirt
import pytest
from fastapi import HTTPException

from app.core import vm_builder, vm_create

creation = importlib.import_module("app.routers.vms.create")


def test_disk_files_are_named_after_the_vm():
    assert vm_builder.disk_file("/p", "web", 0).name == "web.qcow2"
    assert vm_builder.disk_file("/p", "web", 1).name == "web-2.qcow2"


def test_a_disk_is_never_created_over_an_existing_file(tmp_path):
    (tmp_path / "web-2.qcow2").write_bytes(b"the data disk of web")
    with pytest.raises(ValueError, match="already exists"):
        vm_builder.create_disk("web-2", 1, index=0, blank=True, target_dir=tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        vm_builder.create_disk_from_import("web-2", tmp_path / "import.qcow2", target_dir=tmp_path)
    assert (tmp_path / "web-2.qcow2").read_bytes() == b"the data disk of web"


def test_the_creation_refuses_a_name_whose_disk_file_exists(monkeypatch, tmp_path, daemon_libvirt):
    conn = Mock()
    conn.lookupByName.side_effect = libvirt.libvirtError("no domain")
    daemon_libvirt(conn)
    monkeypatch.setattr(vm_create, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(vm_create, "ensure_default_pool", lambda conn: None)
    monkeypatch.setattr(vm_create.firmware, "check_choice", lambda conn, choice: None)
    monkeypatch.setattr(vm_create, "validate_vm_resources", lambda *a: [])
    monkeypatch.setattr(creation, "log_action", Mock())
    monkeypatch.setattr(creation, "create_task", lambda *a, **k: "t")
    created = []
    monkeypatch.setattr(vm_create, "create_disk", lambda *a, **k: created.append(a))
    (tmp_path / "web-2.qcow2").write_bytes(b"the data disk of web")
    payload = creation.VMCreate(
        name="web-2", vcpu=1, memory_mb=512, disks=[{"size_gb": 1}], username="debian", password="secret1"
    )
    with pytest.raises(HTTPException) as error:
        creation._create_vm(payload, {"username": "admin"})
    assert error.value.status_code == 422 and "web-2.qcow2" in str(error.value.detail)
    assert created == []
