"""Hardware added after creation must keep the guest's usable controller type."""

import importlib
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.guest_hardware import guest_profile
from app.core.unattended_install import detect_windows

devices = importlib.import_module("app.routers.vms.devices")
creation = importlib.import_module("app.routers.vms.create")


@pytest.fixture()
def domain(monkeypatch):
    domain = Mock()
    domain.XMLDesc.return_value = """<domain><devices>
      <disk device='disk'><target dev='sda' bus='sata'/></disk>
      <interface><model type='e1000e'/></interface>
    </devices></domain>"""
    domain.isActive.return_value = False
    conn = Mock()
    conn.lookupByName.return_value = domain
    conn.storagePoolLookupByName.return_value.storageVolLookupByName.return_value.path.return_value = (
        "/images/data.qcow2"
    )
    monkeypatch.setattr(devices, "open_conn", lambda: conn)
    monkeypatch.setattr(devices, "log_action", Mock())
    return domain


def test_extra_disk_inherits_sata_controller(domain):
    devices.attach_disk("win", devices.DiskAttach(volume_name="data.qcow2"), {"username": "admin"})
    xml, _ = domain.attachDeviceFlags.call_args.args
    assert ET.fromstring(xml).find("target").attrib == {"dev": "sdb", "bus": "sata"}


def test_sata_disk_attach_requires_shutdown(domain):
    domain.isActive.return_value = True
    with pytest.raises(HTTPException) as error:
        devices.attach_disk("win", devices.DiskAttach(volume_name="data.qcow2"), {"username": "admin"})
    assert error.value.status_code == 409
    domain.attachDeviceFlags.assert_not_called()


def test_extra_interface_inherits_windows_model(domain):
    devices.attach_interface("win", devices.InterfaceAttach(network="default"), {"username": "admin"})
    xml, _ = domain.attachDeviceFlags.call_args.args
    assert ET.fromstring(xml).find("model").get("type") == "e1000e"


@pytest.mark.parametrize("filename", ["Win11_English.iso", "SERVER-2022.iso", "win10.iso", "windows_server_2025.iso"])
def test_windows_filenames(filename):
    assert detect_windows(filename)


@pytest.mark.parametrize("filename", [None, "virtio-win.iso", "darwin.iso", "ubuntu-live-server.iso"])
def test_non_windows_filenames(filename):
    assert not detect_windows(filename)


@pytest.mark.parametrize("field", ["guest_os", "disk_controller"])
def test_unknown_profile_choices_are_rejected(field):
    with pytest.raises(ValidationError):
        creation.VMCreate(name="win", vcpu=2, memory_mb=4096, disks=[{"size_gb": 64}], **{field: "unknown"})


@pytest.mark.parametrize(
    ("iso", "requested", "expected"),
    [
        ("windows-server-2025.iso", "auto", "windows"),
        ("debian-13.iso", "auto", "linux"),
        ("ubuntu-server.iso", "auto", "linux"),
        ("freebsd.iso", "auto", "other"),
        ("installer.iso", "auto", "other"),
        ("installer.iso", "windows", "windows"),
        ("ubuntu-rescue.iso", "other", "other"),
        (None, "windows", "linux"),
    ],
)
def test_guest_profile_defaults_and_overrides(iso, requested, expected):
    assert guest_profile(iso, requested) == expected
