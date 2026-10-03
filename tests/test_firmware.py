"""VM firmware (BIOS, UEFI, UEFI + Secure Boot + TPM 2.0): the domain XML, what the host offers, and what changes
for a UEFI VM (CD drives on SATA, NVRAM and TPM deleted with it, fresh ones for a copy, no snapshot while it runs).
The XML was also defined, started and undefined on a real libvirt 10 with OVMF and swtpm."""

import importlib
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import libvirt
import pytest
from fastapi import HTTPException

from app.core import firmware, vm_builder

runtime = importlib.import_module("app.routers.vms.runtime")
snapshots = importlib.import_module("app.routers.vms.snapshots")


@pytest.fixture(autouse=True)
def no_cluster_probe(monkeypatch):
    monkeypatch.setattr(vm_builder, "_compute_migratable_cpu_xml", lambda: "<cpu mode='host-model'/>")


def build(fw, **kwargs):
    xml = vm_builder.build_domain_xml(
        "vm1",
        2,
        4096,
        ["/var/lib/libvirt/images/vm1.qcow2"],
        "/var/lib/libvirt/images/vm1-cloudinit.iso",
        iso_path="/isos/win11.iso",
        seed_iso_path="/var/lib/libvirt/images/vm1-oemdrv.iso",
        drivers_iso_path="/isos/virtio-win.iso",
        firmware=fw,
        **kwargs,
    )
    return ET.fromstring(xml)


def targets(root):
    return {d.find("source").get("file").rsplit("/", 1)[-1]: d.find("target").attrib for d in root.iter("disk")}


def test_bios_is_the_unchanged_historical_machine():
    root = build(firmware.BIOS)
    assert root.find("os/type").get("machine") == "pc" and root.find("os").get("firmware") is None
    assert root.find("features/smm") is None and root.find("devices/tpm") is None
    assert targets(root)["win11.iso"] == {"dev": "hda", "bus": "ide"}
    assert targets(root)["virtio-win.iso"] == {"dev": "hdd", "bus": "ide"}
    assert firmware.of_domain(root) == firmware.BIOS


def test_uefi_secure_has_q35_secure_boot_smm_and_a_tpm_2():
    root = build(firmware.UEFI_SECURE, disk_bus="sata")
    os_el = root.find("os")
    assert os_el.get("firmware") == "efi" and os_el.find("type").get("machine") == "q35"
    features = {f.get("name"): f.get("enabled") for f in os_el.findall("firmware/feature")}
    assert features == {"secure-boot": "yes", "enrolled-keys": "yes"}
    assert root.find("features/smm").get("state") == "on"
    tpm = root.find("devices/tpm")
    assert tpm.get("model") == "tpm-crb" and tpm.find("backend").attrib == {"type": "emulator", "version": "2.0"}
    assert firmware.of_domain(root) == firmware.UEFI_SECURE and firmware.has_tpm(root)


def test_plain_uefi_asks_for_a_firmware_without_secure_boot():
    root = build(firmware.UEFI)
    features = {f.get("name"): f.get("enabled") for f in root.findall("os/firmware/feature")}
    assert features == {"secure-boot": "no"}
    assert root.find("features/smm") is None and root.find("devices/tpm") is None
    assert firmware.of_domain(root) == firmware.UEFI


@pytest.mark.parametrize("fw", [firmware.UEFI, firmware.UEFI_SECURE])
def test_a_uefi_vm_has_no_ide_bus_its_drives_are_sata_and_the_installer_stays_first(fw):
    found = targets(build(fw, disk_bus="sata"))
    assert not [t for t in found.values() if t["bus"] == "ide"]
    assert found["vm1.qcow2"] == {"dev": "sda", "bus": "sata"}
    drives = [found[n]["dev"] for n in ("win11.iso", "vm1-oemdrv.iso", "vm1-cloudinit.iso", "virtio-win.iso")]
    assert drives == ["sdw", "sdx", "sdy", "sdz"]


def test_an_unknown_firmware_is_refused():
    with pytest.raises(ValueError):
        build("coreboot")


def test_a_uefi_vm_is_undefined_with_its_nvram_and_tpm_a_bios_vm_as_before():
    assert firmware.undefine_flags(build(firmware.BIOS)) == 0
    assert firmware.undefine_flags(build(firmware.UEFI)) == libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
    assert (
        firmware.undefine_flags(build(firmware.UEFI_SECURE))
        == libvirt.VIR_DOMAIN_UNDEFINE_NVRAM | libvirt.VIR_DOMAIN_UNDEFINE_TPM
    )


def test_a_copy_gets_a_fresh_nvram():
    # What libvirt writes back once it picked the firmware: explicit loader and the VM's own NVRAM file.
    root = ET.fromstring(
        "<domain><os firmware='efi'><type machine='q35'>hvm</type>"
        "<loader readonly='yes' secure='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE_4M.ms.fd</loader>"
        "<nvram template='/usr/share/OVMF/OVMF_VARS_4M.ms.fd'>/var/lib/libvirt/qemu/nvram/src_VARS.fd</nvram>"
        "</os><devices/></domain>"
    )
    assert firmware.of_domain(root) == firmware.UEFI_SECURE
    firmware.drop_nvram(root)
    assert root.find("os/nvram") is None and root.find("os/loader") is not None


DOMCAPS = """<domainCapabilities><os supported='yes'>
  <enum name='firmware'>{efi}</enum>
  <loader supported='yes'><enum name='secure'><value>no</value>{secure}</enum></loader></os>
  <devices>{tpm}</devices><features/></domainCapabilities>"""
TPM = """<tpm supported='yes'><enum name='backendModel'><value>passthrough</value>{emulator}</enum>
  <enum name='backendVersion'><value>1.2</value><value>2.0</value></enum></tpm>"""


def caps(efi=True, secure=True, emulator=True):
    return DOMCAPS.format(
        efi="<value>efi</value>" if efi else "",
        secure="<value>yes</value>" if secure else "",
        tpm=TPM.format(emulator="<value>emulator</value>" if emulator else ""),
    )


@pytest.mark.parametrize(
    ("xml", "uefi", "secure", "reason"),
    [
        (caps(), True, True, None),
        (caps(emulator=False), True, False, "swtpm"),
        (caps(secure=False), True, False, "Secure Boot build"),
        (caps(efi=False), False, False, "ovmf"),
    ],
)
def test_the_host_support_comes_from_libvirt_and_names_what_to_install(xml, uefi, secure, reason):
    conn = Mock()
    conn.getDomainCapabilities.return_value = xml
    support = firmware.host_support(conn)
    assert (support["uefi"], support["uefi_secure"]) == (uefi, secure)
    assert (reason is None and support["raison"] is None) or reason in support["raison"]
    assert firmware.check_choice(conn, firmware.BIOS) is None
    assert (firmware.check_choice(conn, firmware.UEFI_SECURE) is None) == secure


def test_without_kvm_the_support_is_read_for_qemu_and_without_either_nothing_is_offered():
    conn = Mock()
    conn.getDomainCapabilities.side_effect = [libvirt.libvirtError("no kvm"), caps()]
    assert firmware.host_support(conn)["uefi_secure"] is True
    conn.getDomainCapabilities.side_effect = libvirt.libvirtError("no q35")
    support = firmware.host_support(conn)
    assert support["uefi"] is False and support["uefi_secure"] is False and "q35" in support["raison"]


def test_the_driver_slot_of_a_uefi_vm_is_its_sata_drive(monkeypatch, tmp_path):
    root = build(firmware.UEFI_SECURE, disk_bus="sata")
    domain = Mock()
    domain.XMLDesc.return_value = ET.tostring(root, encoding="unicode")
    domain.isActive.return_value = True
    conn = Mock()
    conn.lookupByName.return_value = domain
    monkeypatch.setattr(runtime, "open_conn", lambda: conn)
    monkeypatch.setattr(runtime, "log_action", Mock())
    monkeypatch.setattr(runtime, "ISOS_DIR", tmp_path)
    (tmp_path / "virtio.iso").touch()
    runtime.set_vm_cdrom("win", runtime.CdromRequest(iso="virtio.iso", target_dev="hdd"), {"username": "admin"})
    xml, _ = domain.updateDeviceFlags.call_args.args
    assert ET.fromstring(xml).find("target").attrib == {"dev": "sdz", "bus": "sata"}

    runtime.eject_vm_cdrom("win", target_dev="hdd", user={"username": "admin"})
    xml, _ = domain.updateDeviceFlags.call_args.args
    assert ET.fromstring(xml).find("target").get("dev") == "sdz"


@pytest.mark.parametrize(("fw", "active", "refused"), [("uefi_secure", True, True), ("uefi_secure", False, False)])
def test_a_running_uefi_vm_is_not_snapshotted_a_stopped_one_is(monkeypatch, fw, active, refused):
    domain = Mock()
    domain.XMLDesc.return_value = ET.tostring(build(fw), encoding="unicode")
    domain.isActive.return_value = active
    domain.snapshotLookupByName.side_effect = libvirt.libvirtError("none")
    conn = Mock()
    conn.lookupByName.return_value = domain
    monkeypatch.setattr(snapshots, "open_conn", lambda: conn)
    monkeypatch.setattr(snapshots, "log_action", Mock())
    monkeypatch.setattr(snapshots, "_zvol_disks_of_domain", lambda d: [])
    monkeypatch.setattr(snapshots.iscsi, "refuse_if_iscsi", lambda d, what: None)
    monkeypatch.setattr(snapshots, "create_task", lambda *a, **k: "t1")
    monkeypatch.setattr(snapshots.threading, "Thread", Mock())
    payload = snapshots.SnapshotCreate(name="before-update")
    if refused:
        with pytest.raises(HTTPException) as err:
            snapshots.create_snapshot("win", payload, {"username": "admin"})
        assert err.value.status_code == 409 and "Shut it down" in err.value.detail
    else:
        assert snapshots.create_snapshot("win", payload, {"username": "admin"})["task_id"] == "t1"


def test_a_backup_remembers_the_firmware_for_a_restore_to_a_new_vm(database, tmp_path):
    from app.core import backups

    domain = Mock()
    domain.XMLDesc.return_value = ET.tostring(build(firmware.UEFI_SECURE), encoding="unicode")
    backups._write_vm_config(domain, tmp_path)
    assert backups._read_vm_config(tmp_path)["firmware"] == "uefi_secure"
    # A backup made before this option restores as the BIOS VM it was.
    (tmp_path / "vm-config.json").write_text('{"vcpu": 2}')
    assert backups._read_vm_config(tmp_path)["firmware"] == "bios"
