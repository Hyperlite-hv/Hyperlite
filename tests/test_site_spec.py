"""A VM as it goes to another cluster (app/core/site_spec.py): read from its domain at the source, checked at the
target as a VM it creates, and written there as a domain of its own that names only the target's new volumes."""

import copy
import uuid
import xml.etree.ElementTree as ET

import pytest

from app.core import firmware as fw
from app.core import site_spec, vm_builder

VOLUMES = [("/var/lib/libvirt/images/web-0.qcow2", "file"), ("/dev/zvol/tank/web-1", "block")]


def _domain(firmware=fw.BIOS):
    xml = vm_builder.build_domain_xml(
        "web",
        4,
        4096,
        ["/srv/source/web-0.qcow2", ("/dev/zvol/source/web-1", "block")],
        "/srv/source/web-cidata.iso",
        network="lan",
        iso_path="/srv/isos/debian.iso",
        mac="52:54:00:12:34:56",
        firmware=firmware,
    )
    root = ET.fromstring(xml)
    if root.find("uuid") is None:
        ET.SubElement(root, "uuid").text = str(uuid.uuid4())
    return root


SIZES = {"sda": (20 * 1024**3, "qcow2"), "sdb": (10 * 1024**3, "raw")}


def _spec(root):
    return site_spec.from_domain(ET.tostring(root, encoding="unicode"), SIZES)


def test_a_vm_goes_and_names_only_the_targets_volumes():
    root = _domain()
    pin = ET.SubElement(ET.SubElement(root, "cputune"), "vcpupin", vcpu="0", cpuset="3")
    assert pin is not None
    spec, left = _spec(root)
    assert spec["memory_mb"] == 4096 and spec["vcpus"] == 4 and [d["dev"] for d in spec["disks"]] == ["sda", "sdb"]
    assert spec["nics"][0]["mac"] == "52:54:00:12:34:56" and spec["nics"][0]["network"] == "lan"
    assert any("CPU pinning" in x for x in left) and any("ISO" in x for x in left)
    checked = site_spec.check(spec)
    xml = site_spec.to_domain(checked, VOLUMES, ["prod"])
    assert "/srv/" not in xml and "cputune" not in xml
    out = ET.fromstring(xml)
    assert [s.get("file") or s.get("dev") for s in out.findall("devices/disk/source")] == [p for p, _ in VOLUMES]
    assert out.find("devices/interface/source").get("network") == "prod"
    assert (
        out.findtext("uuid") == spec["uuid"]
        and out.find("devices/interface/mac").get("address") == spec["nics"][0]["mac"]
    )
    assert out.find("devices/disk[@device='cdrom']/source") is None  # the drives go empty


def test_uefi_with_secure_boot_gets_a_fresh_nvram_and_tpm():
    spec, left = _spec(_domain(fw.UEFI_SECURE))
    assert spec["firmware"] == fw.UEFI_SECURE and spec["machine"] == "q35"
    assert any("TPM" in x for x in left) and any("NVRAM" in x for x in left)
    out = ET.fromstring(site_spec.to_domain(site_spec.check(spec), VOLUMES, ["prod"]))
    assert fw.of_domain(out) == fw.UEFI_SECURE and fw.has_tpm(out) and out.find("os/nvram") is None


def test_a_vm_with_a_device_of_this_host_is_refused():
    root = _domain()
    devices = root.find("devices")
    assert site_spec.refusals(root) == []
    ET.SubElement(devices, "hostdev", mode="subsystem", type="pci")
    fs = ET.SubElement(devices, "filesystem", type="mount")
    ET.SubElement(fs, "source", dir="/etc")
    lun = ET.SubElement(devices, "disk", type="network", device="disk")
    ET.SubElement(lun, "target", dev="sdc", bus="scsi")
    reasons = " ".join(site_spec.refusals(root))
    assert "passthrough" in reasons and "directory" in reasons and "sdc" in reasons


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s.update(name="../etc"), "name"),
        (lambda s: s.update(uuid="nope"), "uuid"),
        (lambda s: s.update(memory_mb=10**12), "memory_mb"),
        (lambda s: s.update(firmware="coreboot"), "firmware"),
        (lambda s: s.update(machine="pc", firmware=fw.UEFI), "q35"),
        (lambda s: s["disks"][0].update(dev="../../x"), "drive name"),
        (lambda s: s["disks"][0].update(bus="usb"), "bus"),
        (lambda s: s["disks"][0].update(size=-1), "size"),
        (lambda s: s["disks"][0].update(format="vmdk"), "format"),
        (lambda s: s["disks"][0].update(iotune={"source": "/etc/shadow"}), "unknown limits"),
        (lambda s: s["disks"][0].update(serial="a b'"), "serial"),
        (lambda s: s["disks"][1].update(dev="sda"), "same target"),
        (lambda s: s["nics"][0].update(mac="01:00:5e:00:00:01"), "unicast"),
        (lambda s: s["nics"].append(dict(s["nics"][0])), "same MAC"),
        (lambda s: s["cpu"].update(mode="custom", model="<x/>"), "CPU model"),
        (lambda s: s["cpu"].update(features=[{"name": "sse4.2", "policy": "steal"}]), "policy"),
        (lambda s: s.update(disks=[]), "between 1"),
        (lambda s: s.update(graphics="rdp"), "graphics"),
    ],
)
def test_the_target_checks_every_field(change, message):
    spec, _ = _spec(_domain())
    bad = copy.deepcopy(spec)
    change(bad)
    with pytest.raises(site_spec.SpecError, match=message):
        site_spec.check(bad)


def test_what_is_not_carried_never_reaches_the_target():
    spec, _ = _spec(_domain())
    spec["emulator"] = "/srv/evil-qemu"
    spec["disks"][0]["source"] = "/etc/shadow"
    spec["nics"][0]["script"] = "/bin/sh"
    xml = site_spec.to_domain(site_spec.check(spec), VOLUMES, ["prod"])
    assert "evil" not in xml and "shadow" not in xml and "/bin/sh" not in xml
    with pytest.raises(site_spec.SpecError, match="A volume for every disk"):
        site_spec.to_domain(site_spec.check(spec), VOLUMES[:1], ["prod"])
