"""PCI and USB passthrough: the host inventory, the devices the host itself needs (never given away), IOMMU groups
given whole, and the endpoints. sysfs and procfs are a fake tree; the <hostdev> XML was also attached to and
detached from a real libvirt 10 domain, and the host checks were run against a real host's disks and network card."""

import subprocess
import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import passthrough
from app.routers import host as host_router
from app.routers.vms import devices


def pci_xml(bus, fn, cls, vendor, product, group, members, driver="nouveau"):
    addrs = "".join(f"<address domain='0x0000' bus='0x{b:02x}' slot='0x00' function='0x{f:x}'/>" for b, f in members)
    return (
        f"<device><name>pci_0000_{bus:02x}_00_{fn}</name><path>/sys/devices/pci0000:00/0000:{bus:02x}:00.{fn}</path>"
        f"<driver><name>{driver}</name></driver><capability type='pci'><class>{cls}</class><domain>0</domain>"
        f"<bus>{bus}</bus><slot>0</slot><function>{fn}</function><product id='{product}'>Product {product}</product>"
        f"<vendor id='{vendor}'>Vendor {vendor}</vendor><iommuGroup number='{group}'>{addrs}</iommuGroup></capability></device>"
    )


def usb_xml(bus, dev, vendor, product):
    return (
        f"<device><name>usb_{bus}_{dev}</name><path>/sys/devices/usb{bus}/{bus}-{dev}</path><capability type='usb_device'>"
        f"<bus>{bus}</bus><device>{dev}</device><product id='{product}'>Stick</product><vendor id='{vendor}'>Maker</vendor>"
        "</capability></device>"
    )


NODEDEVS = [
    pci_xml(0, 0, "0x060400", "0x8086", "0x1234", 0, [(0, 0)], driver="pcieport"),  # bridge
    pci_xml(1, 0, "0x030000", "0x10de", "0x1b80", 1, [(1, 0), (1, 1)]),  # spare GPU
    pci_xml(1, 1, "0x040300", "0x10de", "0x10f0", 1, [(1, 0), (1, 1)], driver="snd_hda_intel"),  # its audio
    pci_xml(2, 0, "0x030000", "0x1002", "0x6798", 2, [(2, 0)]),  # host console GPU
    pci_xml(3, 0, "0x020000", "0x8086", "0x1533", 3, [(3, 0)], driver="igb"),  # management NIC
    pci_xml(4, 0, "0x010802", "0x144d", "0xa808", 4, [(4, 0)], driver="nvme"),  # boot disk controller
    pci_xml(5, 0, "0x020000", "0x8086", "0x1533", 5, [(5, 0)], driver="igb"),  # spare NIC
    usb_xml(1, 1, "0x1d6b", "0x0002"),  # root hub
    usb_xml(1, 4, "0x0781", "0x5591"),
    usb_xml(2, 3, "0x1050", "0x0407"),
    usb_xml(2, 5, "0x1050", "0x0407"),  # an identical second key
]


@pytest.fixture()
def host(tmp_path, monkeypatch):
    sys_root, proc = tmp_path / "sys", tmp_path / "proc"
    dev = sys_root / "devices" / "pci0000:00"
    for bus in range(6):
        (dev / f"0000:{bus:02x}:00.0").mkdir(parents=True)
    (dev / "0000:01:00.1").mkdir()
    (dev / "0000:01:00.0" / "boot_vga").write_text("0\n")
    (dev / "0000:02:00.0" / "boot_vga").write_text("1\n")
    (dev / "0000:03:00.0" / "net" / "eno1").mkdir(parents=True)
    (dev / "0000:05:00.0" / "net" / "eno2").mkdir(parents=True)
    (dev / "0000:04:00.0" / "nvme" / "nvme0" / "block" / "nvme0n1" / "nvme0n1p2").mkdir(parents=True)
    (sys_root / "class" / "net" / "eno1").mkdir(parents=True)
    (sys_root / "class" / "net" / "eno1" / "master").write_text("vmbr0")
    (sys_root / "class" / "net" / "eno2").mkdir(parents=True)
    (sys_root / "kernel" / "iommu_groups" / "1").mkdir(parents=True)
    proc.mkdir()
    (proc / "mounts").write_text("/dev/nvme0n1p2 / ext4 rw 0 0\nproc /proc proc rw 0 0\n")
    (proc / "swaps").write_text("Filename Type Size Used Priority\n")
    monkeypatch.setattr(passthrough, "SYS", sys_root)
    monkeypatch.setattr(passthrough, "PROC", proc)
    monkeypatch.setattr(passthrough.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        passthrough.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )
    return sys_root


class NodeDev:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags):
        return self.xml


class Domain:
    def __init__(self, name, active=False):
        self._name = name
        self.active = active
        self.hostdevs = []

    def name(self):
        return self._name

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        return f"<domain><name>{self._name}</name><devices>{''.join(self.hostdevs)}</devices></domain>"

    def attachDeviceFlags(self, xml, flags):
        self.hostdevs.append(xml)

    def detachDeviceFlags(self, xml, flags):
        # libvirt matches the device, not the text: compare what the <hostdev> points at.
        gone = set(passthrough._hostdev_keys(ET.fromstring(xml)))
        self.hostdevs = [h for h in self.hostdevs if not gone & set(passthrough._hostdev_keys(ET.fromstring(h)))]


class Conn:
    def __init__(self):
        self.domains = {"vm1": Domain("vm1"), "other": Domain("other")}

    def listAllDevices(self, flags):
        return [NodeDev(x) for x in NODEDEVS]

    def listAllDomains(self, flags):
        return list(self.domains.values())

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


def test_the_inventory_says_what_the_host_needs_and_hides_root_hubs(host):
    inv = passthrough.list_devices(Conn())
    pci = {d["adresse"]: d for d in inv["pci"]}
    assert inv["iommu"]["actif"] is True
    assert pci["0000:00:00.0"]["hote"].startswith("PCI bridge")
    assert pci["0000:02:00.0"]["hote"] == "Graphics card showing the host console"
    assert "eno1" in pci["0000:03:00.0"]["hote"]
    assert "nvme0n1p2" in pci["0000:04:00.0"]["hote"]
    assert pci["0000:01:00.0"]["hote"] is None and pci["0000:05:00.0"]["hote"] is None
    assert pci["0000:01:00.0"]["groupe"] == ["pci_0000_01_00_0", "pci_0000_01_00_1"]
    assert pci["0000:01:00.0"]["ids"] == "10de:1b80"
    assert [d["adresse"] for d in inv["usb"]] == ["001:004", "002:003", "002:005"]


def test_a_gpu_is_given_with_its_whole_iommu_group(host):
    plan = passthrough.plan_attach(Conn(), "vm1", "pci_0000_01_00_1", running=False)
    assert [d["id"] for d, _ in plan] == ["pci_0000_01_00_0", "pci_0000_01_00_1"]
    assert "<address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/>" in plan[1][1]
    assert "managed='yes'" in plan[0][1]


@pytest.mark.parametrize(
    ("device", "running", "status", "text"),
    [
        ("pci_0000_02_00_0", False, 409, "host console"),
        ("pci_0000_03_00_0", False, 409, "eno1"),
        ("pci_0000_04_00_0", False, 409, "nvme0n1p2"),
        ("pci_0000_00_00_0", False, 409, "PCI bridge"),
        ("pci_0000_05_00_0", True, 409, "Shut down the VM"),
        ("pci_0000_09_00_0", False, 404, "not found"),
        ("../../etc/passwd", False, 422, "Invalid"),
    ],
)
def test_refusals_name_the_reason(host, device, running, status, text):
    with pytest.raises(passthrough.PassthroughError) as err:
        passthrough.plan_attach(Conn(), "vm1", device, running)
    assert err.value.status == status and text in err.value.message


def test_without_iommu_a_pci_device_is_refused_with_the_steps_but_usb_still_works(host):
    (host / "kernel" / "iommu_groups" / "1").rmdir()
    with pytest.raises(passthrough.PassthroughError) as err:
        passthrough.plan_attach(Conn(), "vm1", "pci_0000_05_00_0", running=False)
    assert "intel_iommu=on" in err.value.message
    plan = passthrough.plan_attach(Conn(), "vm1", "usb_1_4", running=True)
    assert "<vendor id='0x0781'/><product id='0x5591'/>" in plan[0][1]


def test_identical_usb_devices_are_told_apart_by_their_address(host):
    plan = passthrough.plan_attach(Conn(), "vm1", "usb_2_5", running=False)
    assert "<address bus='2' device='5'/>" in plan[0][1]


def test_a_device_another_vm_has_is_refused_also_through_its_group(host):
    conn = Conn()
    conn.domains["other"].hostdevs.append(passthrough._pci_hostdev_xml("pci_0000_01_00_1"))
    with pytest.raises(passthrough.PassthroughError) as err:
        passthrough.plan_attach(conn, "vm1", "pci_0000_01_00_0", running=False)
    assert "0000:01:00.1 (same IOMMU group)" in err.value.message and "'other'" in err.value.message


@pytest.fixture()
def api(host, client, auth_headers, monkeypatch):
    conn = Conn()
    monkeypatch.setattr(devices, "open_conn", lambda *a, **k: conn)
    monkeypatch.setattr(host_router, "open_conn", lambda *a, **k: conn)
    return {"client": client, "headers": auth_headers("admin1"), "conn": conn}


def test_a_pci_device_needs_a_confirmation_then_is_given_and_taken_back(api):
    c, h, vm = api["client"], api["headers"], api["conn"].domains["vm1"]
    r = c.post("/vms/vm1/hostdevs", json={"device": "pci_0000_01_00_0"}, headers=h)
    assert r.status_code == 422 and "confirm" in r.json()["detail"] and vm.hostdevs == []
    r = c.post("/vms/vm1/hostdevs", json={"device": "pci_0000_01_00_0", "confirm": True}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["ajoutes"] == ["pci_0000_01_00_0", "pci_0000_01_00_1"]
    listed = c.get("/vms/vm1/hostdevs", headers=h).json()
    assert [(d["adresse"], d["present"]) for d in listed] == [("0000:01:00.0", True), ("0000:01:00.1", True)]
    assert c.delete("/vms/vm1/hostdevs/pci_0000_01_00_1", headers=h).status_code == 200
    assert [d["id"] for d in c.get("/vms/vm1/hostdevs", headers=h).json()] == ["pci_0000_01_00_0"]


def test_a_usb_device_goes_to_a_running_vm_and_the_host_inventory_shows_who_has_it(api):
    c, h = api["client"], api["headers"]
    api["conn"].domains["vm1"].active = True
    assert c.post("/vms/vm1/hostdevs", json={"device": "usb_1_4"}, headers=h).status_code == 201
    stick = next(d for d in c.get("/host/devices", headers=h).json()["usb"] if d["id"] == "usb_1_4")
    assert stick["vm"] == "vm1"
    r = c.post("/vms/other/hostdevs", json={"device": "usb_1_4"}, headers=h)
    assert r.status_code == 409 and "'vm1'" in r.json()["detail"]


def test_only_an_administrator_touches_host_devices(api, auth_headers):
    headers = auth_headers("watcher", role="observateur")
    assert api["client"].get("/host/devices", headers=headers).status_code == 403
    assert api["client"].post("/vms/vm1/hostdevs", json={"device": "usb_1_4"}, headers=headers).status_code == 403
