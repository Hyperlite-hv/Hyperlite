"""A VM created from an installation ISO is installed by hand from the console, as on Proxmox VE, whatever the system:
Hyperlite no longer answers the installers itself (Kickstart, autoinstall, removed before 1.0)."""

import importlib
from unittest.mock import Mock

import libvirt
import pytest

from app.core import disk_library, vm_create

creation = importlib.import_module("app.routers.vms.create")


@pytest.fixture()
def created(monkeypatch, tmp_path, daemon_libvirt):
    conn = Mock()
    conn.lookupByName.side_effect = libvirt.libvirtError("no domain")
    domain = Mock()
    conn.defineXML.return_value = domain
    built, tasks = {}, []
    daemon_libvirt(conn)
    monkeypatch.setattr(creation, "open_conn", Mock)  # the summary of what the daemon made
    monkeypatch.setattr(disk_library, "ISOS", tmp_path)
    monkeypatch.setattr(vm_create.firmware, "check_choice", lambda conn, choice: None)
    monkeypatch.setattr(vm_create, "validate_vm_resources", lambda *a: [])
    monkeypatch.setattr(vm_create, "generate_mac", lambda conn: "52:54:00:00:00:01")
    monkeypatch.setattr(vm_create, "allocate_static_ip", lambda *a: None)
    monkeypatch.setattr(
        vm_create, "create_disk", lambda name, size, index, blank, target_dir: f"/images/{name}-{index}"
    )
    monkeypatch.setattr(vm_create, "build_domain_xml", lambda *a, **k: built.update(args=a, kwargs=k) or "<domain/>")
    monkeypatch.setattr(vm_create, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(creation, "_domain_summary", lambda domain: {"name": "srv"})
    monkeypatch.setattr(creation, "create_task", lambda kind, *a, **k: tasks.append(kind) or f"t-{kind}")
    for name in ("set_vm_ssh_user", "set_vm_os_label", "set_vm_auto_cleanup", "log_action"):
        monkeypatch.setattr(creation, name, Mock())

    def create(iso, **fields):
        (tmp_path / iso).touch()
        payload = creation.VMCreate(name="srv", vcpu=2, memory_mb=2048, disks=[{"size_gb": 20}], iso=iso, **fields)
        creation._create_vm(payload, {"username": "admin"})
        return built, tasks, domain

    return create


@pytest.mark.parametrize("iso", ["Rocky-9.5-x86_64-dvd.iso", "ubuntu-24.04-live-server-amd64.iso", "debian-13.iso"])
def test_any_iso_is_installed_by_hand_with_no_account_asked_and_no_answers(created, iso):
    built, tasks, domain = created(iso)  # no username nor password: the installer asks for them
    args, kwargs = built["args"], built["kwargs"]
    assert args[4] is None  # no cloud-init
    assert set(kwargs) & {"seed_iso_path", "kernel_path", "initrd_path", "kernel_cmdline"} == set()
    assert kwargs["iso_path"].name == iso
    assert tasks == ["create_vm"]  # no unattended installation task
    domain.create.assert_not_called()  # started by the user, who then follows the installer in the console
    creation.set_vm_ssh_user.assert_not_called()
