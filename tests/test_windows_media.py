"""Driver media must never overwrite the installation DVD or a data disk."""

import importlib
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import libvirt
import pytest
from fastapi import HTTPException

creation = importlib.import_module("app.routers.vms.create")
runtime = importlib.import_module("app.routers.vms.runtime")

INSTALLER = """<disk type='file' device='cdrom'><driver name='qemu' type='raw'/>
<source file='/isos/windows.iso'/><target dev='hda' bus='ide'/><readonly/></disk>"""
DRIVERS = """<disk type='file' device='cdrom'><driver name='qemu' type='raw'/>
<source file='/isos/old-drivers.iso'/><target dev='hdd' bus='ide'/><readonly/></disk>"""


@pytest.fixture()
def domain(monkeypatch, tmp_path):
    domain = Mock()
    domain.XMLDesc.return_value = f"<domain><devices>{INSTALLER}{DRIVERS}</devices></domain>"
    domain.isActive.return_value = True
    conn = Mock()
    conn.lookupByName.return_value = domain
    monkeypatch.setattr(runtime, "open_conn", lambda: conn)
    monkeypatch.setattr(runtime, "log_action", Mock())
    monkeypatch.setattr(runtime, "ISOS_DIR", tmp_path)
    (tmp_path / "virtio.iso").touch()
    return domain


def mount():
    return runtime.set_vm_cdrom("win", runtime.CdromRequest(iso="virtio.iso", target_dev="hdd"), {"username": "admin"})


def test_mount_driver_media_changes_only_second_cd_and_persists(domain):
    mount()
    xml, flags = domain.updateDeviceFlags.call_args.args
    disk = ET.fromstring(xml)
    assert disk.find("target").get("dev") == "hdd"
    assert disk.find("source").get("file").endswith("virtio.iso")
    assert flags == libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_AFFECT_LIVE
    domain.attachDeviceFlags.assert_not_called()


def test_new_driver_cd_requires_stopped_vm(domain):
    domain.XMLDesc.return_value = f"<domain><devices>{INSTALLER}</devices></domain>"
    with pytest.raises(HTTPException) as error:
        mount()
    assert error.value.status_code == 409
    domain.attachDeviceFlags.assert_not_called()
    domain.updateDeviceFlags.assert_not_called()


def test_new_driver_cd_on_stopped_vm_keeps_installation_cd(domain):
    domain.XMLDesc.return_value = f"<domain><devices>{INSTALLER}</devices></domain>"
    domain.isActive.return_value = False
    mount()
    xml, flags = domain.attachDeviceFlags.call_args.args
    disk = ET.fromstring(xml)
    assert disk.find("target").attrib == {"dev": "hdd", "bus": "ide"}
    assert disk.find("boot") is None
    assert disk.find("readonly") is not None
    assert flags == libvirt.VIR_DOMAIN_AFFECT_CONFIG
    domain.updateDeviceFlags.assert_not_called()


def test_driver_target_cannot_replace_a_hard_disk(domain):
    hard_disk = "<disk device='disk'><target dev='hdd' bus='ide'/></disk>"
    domain.XMLDesc.return_value = f"<domain><devices>{INSTALLER}{hard_disk}</devices></domain>"
    with pytest.raises(HTTPException) as error:
        mount()
    assert error.value.status_code == 409
    domain.attachDeviceFlags.assert_not_called()
    domain.updateDeviceFlags.assert_not_called()


def test_eject_driver_media_does_not_eject_windows_dvd(domain):
    runtime.eject_vm_cdrom("win", target_dev="hdd", user={"username": "admin"})
    xml, _ = domain.updateDeviceFlags.call_args.args
    disk = ET.fromstring(xml)
    assert disk.find("target").get("dev") == "hdd"
    assert disk.find("source") is None


def test_legacy_mount_without_target_still_selects_first_cd(domain):
    runtime.set_vm_cdrom("win", runtime.CdromRequest(iso="virtio.iso"), {"username": "admin"})
    xml, _ = domain.updateDeviceFlags.call_args.args
    assert ET.fromstring(xml).find("target").get("dev") == "hda"


@pytest.mark.parametrize(
    "drivers_iso", ["../outside.iso", "/etc/driver.iso", "..\\outside.iso", "bad.txt", "missing.iso"]
)
def test_invalid_driver_media_is_rejected_before_allocation(monkeypatch, tmp_path, drivers_iso):
    monkeypatch.setattr(creation, "ISOS_DIR", tmp_path)
    monkeypatch.setattr(creation, "validate_vm_resources", lambda *args: [])
    connection = Mock(side_effect=AssertionError("Must not allocate resources"))
    monkeypatch.setattr(creation, "open_conn", connection)
    (tmp_path / "windows.iso").touch()
    payload = creation.VMCreate(
        name="win", vcpu=2, memory_mb=4096, disks=[{"size_gb": 64}], iso="windows.iso", drivers_iso=drivers_iso
    )
    with pytest.raises(HTTPException) as error:
        creation.create_vm(payload, {"username": "admin"})
    assert error.value.status_code == 422
    connection.assert_not_called()
