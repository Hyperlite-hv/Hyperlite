"""Host device passthrough: give a VM a physical PCI device (GPU, NIC, storage or USB controller) or a USB device.

PCI passthrough uses VFIO: while the VM runs, libvirt unbinds the device from its host driver (managed='yes') and
the host can no longer use it. Two things follow, both checked here before anything is changed:
  - the IOMMU must be on (VT-d / AMD-Vi in the firmware, and on the kernel command line); without it libvirt fails
    at the VM's start, so the attach is refused up front with the steps to enable it;
  - a device the host itself needs must never be taken: the graphics card that shows the host console, a network
    card that carries an address or sits in a bridge (the way to reach the server), a disk controller with a mounted
    file system, swap or a ZFS pool on it, and PCI bridges. Refused with the reason.

IOMMU groups: the devices of a group can only go to a VM together (a GPU and its HDMI audio function, typically).
Attaching one device attaches every endpoint of its group; the group is refused if one of its devices is used by the
host or by another VM.

USB devices are matched by vendor and product id (so they survive being unplugged and plugged into another port),
or by bus and address when two identical devices are connected. They can be attached to a running VM.

A VM with a host device cannot be live-migrated (libvirt refuses): the device is in this server.
"""

import logging
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

logger = logging.getLogger(__name__)

# Overridable in tests: every sysfs and procfs read goes through these.
SYS = Path("/sys")
PROC = Path("/proc")

DEVICE_ID_RE = re.compile(r"^(pci_[0-9a-f]{4}_[0-9a-f]{2}_[0-9a-f]{2}_[0-7]|usb_\d{1,3}_\d{1,3}(_[\d_.]{1,40})?)$")
LINUX_FOUNDATION = "0x1d6b"  # USB root hubs: the host's own controllers, never a device to hand over


class PassthroughError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def _sys_path(path):
    """A sysfs path from a node device XML (/sys/devices/...), under SYS (tests point it elsewhere)."""
    rel = Path(path).relative_to("/sys") if path and str(path).startswith("/sys/") else None
    return SYS / rel if rel is not None else None


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def iommu_state():
    """{"actif": bool, "raison": str|None}. The kernel creates /sys/kernel/iommu_groups/* only when the IOMMU is on."""
    groups = SYS / "kernel" / "iommu_groups"
    try:
        active = groups.is_dir() and any(groups.iterdir())
    except OSError:
        active = False
    if active:
        return {"actif": True, "raison": None}
    return {
        "actif": False,
        "raison": "The IOMMU is off: enable VT-d (Intel) or AMD-Vi/IOMMU (AMD) in the server's firmware, add "
        "intel_iommu=on iommu=pt (Intel) or amd_iommu=on iommu=pt (AMD) to the kernel command line, then reboot",
    }


# --- What the host uses ----------------------------------------------------------------------------------------


def _mounted_sources():
    sources = set()
    for name in ("mounts", "swaps"):
        text = _read(PROC / name) or ""
        for line in text.splitlines():
            first = line.split(" ", 1)[0]
            if first.startswith("/dev/"):
                # /dev/mapper/vg-root is a link to /dev/dm-0: keep both spellings.
                sources.update({first, os.path.realpath(first)})
    if shutil.which("zpool"):
        try:
            out = subprocess.run(["zpool", "status", "-LP"], capture_output=True, text=True, timeout=10).stdout
            sources.update(w for w in out.split() if w.startswith("/dev/"))
        except (OSError, subprocess.SubprocessError):
            logger.debug("zpool status failed", exc_info=True)
    return sources


def _block_names(device_dir):
    """Block devices below a device (disks of a controller, a USB stick), with their partitions and holders (LVM,
    RAID, dm-crypt), as kernel names ("sda", "sda1", "dm-0")."""
    names = set()
    for block_dir in device_dir.rglob("block"):
        if not block_dir.is_dir():
            continue
        for disk in block_dir.iterdir():
            names.add(disk.name)
            for child in disk.iterdir():
                if child.is_dir() and child.name.startswith(disk.name):
                    names.add(child.name)
    pending = list(names)
    while pending:
        holders = SYS / "class" / "block" / pending.pop() / "holders"
        if holders.is_dir():
            for holder in holders.iterdir():
                if holder.name not in names:
                    names.add(holder.name)
                    pending.append(holder.name)
    return names


def _dev_paths(name):
    """The /dev paths a kernel block name can be mounted under."""
    paths = {f"/dev/{name}"}
    dm_name = _read(SYS / "class" / "block" / name / "dm" / "name")
    if dm_name:
        paths.add(f"/dev/mapper/{dm_name}")
    return paths


def _interfaces(device_dir):
    return sorted({p.name for d in device_dir.rglob("net") if d.is_dir() for p in d.iterdir()})


def _interface_in_use(ifname):
    """An interface with an address, or enslaved to a bridge or bond: how the server is reached."""
    if (SYS / "class" / "net" / ifname / "master").exists():
        return True
    try:
        out = subprocess.run(["ip", "-o", "addr", "show", "dev", ifname], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.debug("ip addr failed for %s", ifname, exc_info=True)
        return True  # unknown: never risk the way into the server
    return " inet" in out.stdout


def host_use(kind, pci_class, sysfs_path):
    """Why the host needs this device, or None."""
    device_dir = _sys_path(sysfs_path)
    if kind == "pci" and pci_class and pci_class.startswith("0x06"):
        return "PCI bridge: part of the host's own bus"
    if device_dir is None or not device_dir.exists():
        return None
    if kind == "pci" and _read(device_dir / "boot_vga") == "1":
        return "Graphics card showing the host console"
    for ifname in _interfaces(device_dir):
        if _interface_in_use(ifname):
            return f"Network card {ifname} is in use by the host (it has an address or is in a bridge)"
    blocks = _block_names(device_dir)
    if blocks:
        mounted = _mounted_sources()
        for name in sorted(blocks):
            if _dev_paths(name) & mounted:
                return f"Holds {name}, which the host uses (mounted, swap or ZFS pool)"
    return None


# --- Listing ---------------------------------------------------------------------------------------------------


def _text(el, path):
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def _hex(value):
    return f"{int(value, 0):x}" if value else "0"


def _pci_address(cap):
    return "{:04x}:{:02x}:{:02x}.{:x}".format(
        *(int(_text(cap, t) or "0") for t in ("domain", "bus", "slot", "function"))
    )


def _parse(dev_xml):
    root = ET.fromstring(dev_xml)
    name = root.findtext("name")
    driver = root.findtext("driver/name")
    path = root.findtext("path")
    pci = root.find("capability[@type='pci']")
    if pci is not None:
        group = pci.find("iommuGroup")
        members = []
        if group is not None:
            for a in group.findall("address"):
                members.append(
                    "pci_{:04x}_{:02x}_{:02x}_{:x}".format(
                        *(int(a.get(k), 16) for k in ("domain", "bus", "slot", "function"))
                    )
                )
        return {
            "id": name,
            "type": "pci",
            "adresse": _pci_address(pci),
            "classe": _text(pci, "class"),
            "fabricant": _text(pci, "vendor") or pci.find("vendor").get("id"),
            "produit": _text(pci, "product") or pci.find("product").get("id"),
            "ids": f"{_hex(pci.find('vendor').get('id'))}:{_hex(pci.find('product').get('id'))}",
            "pilote": driver,
            "groupe_iommu": int(group.get("number")) if group is not None else None,
            "groupe": members,
            "chemin": path,
        }
    usb = root.find("capability[@type='usb_device']")
    if usb is not None:
        vendor, product = usb.find("vendor"), usb.find("product")
        return {
            "id": name,
            "type": "usb",
            "adresse": f"{int(_text(usb, 'bus') or 0):03d}:{int(_text(usb, 'device') or 0):03d}",
            "bus": int(_text(usb, "bus") or 0),
            "numero": int(_text(usb, "device") or 0),
            "fabricant": _text(usb, "vendor") or vendor.get("id"),
            "produit": _text(usb, "product") or product.get("id"),
            "vendor_id": vendor.get("id"),
            "product_id": product.get("id"),
            "ids": f"{_hex(vendor.get('id'))}:{_hex(product.get('id'))}",
            "pilote": driver,
            "chemin": path,
        }
    return None


def _assignments(conn):
    """{device id: VM name} for every hostdev in every VM's persistent definition."""
    taken = {}
    for domain in conn.listAllDomains(0):
        try:
            root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except (libvirt.libvirtError, ET.ParseError):
            logger.debug("Cannot read %s", domain.name(), exc_info=True)
            continue
        for hostdev in root.findall("./devices/hostdev"):
            for key in _hostdev_keys(hostdev):
                taken[key] = domain.name()
    return taken


def _hostdev_keys(hostdev):
    """Identifiers a <hostdev> can match: a PCI node device name, or USB "ids:" / "addr:" keys."""
    source = hostdev.find("source")
    if source is None:
        return []
    if hostdev.get("type") == "pci":
        a = source.find("address")
        if a is None:
            return []
        return [
            "pci_{:04x}_{:02x}_{:02x}_{:x}".format(
                *(int(a.get(k, "0"), 16) for k in ("domain", "bus", "slot", "function"))
            )
        ]
    if hostdev.get("type") == "usb":
        keys = []
        vendor, product, addr = source.find("vendor"), source.find("product"), source.find("address")
        if vendor is not None and product is not None:
            keys.append(f"ids:{int(vendor.get('id'), 16):04x}:{int(product.get('id'), 16):04x}")
        if addr is not None:
            keys.append(f"addr:{int(addr.get('bus'))}:{int(addr.get('device'))}")
        return keys
    return []


def _usb_keys(dev):
    return [
        f"ids:{int(dev['vendor_id'], 16):04x}:{int(dev['product_id'], 16):04x}",
        f"addr:{dev['bus']}:{dev['numero']}",
    ]


def list_devices(conn):
    """{"iommu": iommu_state(), "pci": [...], "usb": [...]}; each device says who uses it: "vm" (a VM's name) and
    "hote" (why the host needs it), both None when it is free."""
    flags = libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_PCI_DEV | libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_USB_DEV
    devices = []
    for nodedev in conn.listAllDevices(flags):
        try:
            dev = _parse(nodedev.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError, AttributeError, ValueError):
            logger.debug("Unreadable node device", exc_info=True)
            continue
        if dev is None or (dev["type"] == "usb" and dev["vendor_id"] == LINUX_FOUNDATION):
            continue
        devices.append(dev)
    taken = _assignments(conn)
    for dev in devices:
        keys = [dev["id"]] if dev["type"] == "pci" else _usb_keys(dev)
        dev["vm"] = next((taken[k] for k in keys if k in taken), None)
        dev["hote"] = host_use(dev["type"], dev.get("classe"), dev.get("chemin"))
        dev.pop("chemin", None)
    return {
        "iommu": iommu_state(),
        "pci": sorted((d for d in devices if d["type"] == "pci"), key=lambda d: d["adresse"]),
        "usb": sorted((d for d in devices if d["type"] == "usb"), key=lambda d: d["adresse"]),
    }


# --- Attaching -------------------------------------------------------------------------------------------------


def _pci_hostdev_xml(device_id):
    domain, bus, slot, function = device_id.split("_")[1:]
    return (
        "<hostdev mode='subsystem' type='pci' managed='yes'><source>"
        f"<address domain='0x{domain}' bus='0x{bus}' slot='0x{slot}' function='0x{function}'/>"
        "</source></hostdev>"
    )


def _usb_hostdev_xml(dev, by_address):
    if by_address:
        source = f"<address bus='{dev['bus']}' device='{dev['numero']}'/>"
    else:
        source = f"<vendor id='{dev['vendor_id']}'/><product id='{dev['product_id']}'/>"
    return f"<hostdev mode='subsystem' type='usb' managed='yes'><source>{source}</source></hostdev>"


def plan_attach(conn, vm_name, device_id, running):
    """The <hostdev> XML to attach for this device (all of its IOMMU group for PCI), or PassthroughError."""
    if not DEVICE_ID_RE.match(device_id or ""):
        raise PassthroughError("Invalid device identifier")
    inventory = list_devices(conn)
    by_id = {d["id"]: d for d in inventory["pci"] + inventory["usb"]}
    dev = by_id.get(device_id)
    if dev is None:
        raise PassthroughError(f"Device '{device_id}' not found on this host", 404)

    if dev["type"] == "usb":
        if dev["hote"]:
            raise PassthroughError(f"The host uses this device: {dev['hote']}", 409)
        if dev["vm"]:
            raise PassthroughError(f"This device is already given to VM '{dev['vm']}'", 409)
        twins = [d for d in inventory["usb"] if d["ids"] == dev["ids"]]
        return [(dev, _usb_hostdev_xml(dev, by_address=len(twins) > 1))]

    if running:
        raise PassthroughError("Shut down the VM before giving it a PCI device", 409)
    if not inventory["iommu"]["actif"]:
        raise PassthroughError(inventory["iommu"]["raison"], 409)
    if dev["hote"]:
        # Checked on the device asked for first: a bridge is left out of its group's endpoints below, and must
        # still be refused rather than give an empty attach.
        raise PassthroughError(f"This device is needed by the host: {dev['hote']}", 409)
    members = [by_id[m] for m in dev["groupe"] if m in by_id] or [dev]
    endpoints = [m for m in members if not (m.get("classe") or "").startswith("0x06")]
    for member in endpoints:
        if member["hote"]:
            what = "This device" if member["id"] == device_id else f"{member['adresse']} (same IOMMU group)"
            raise PassthroughError(f"{what} is needed by the host: {member['hote']}", 409)
        if member["vm"] and member["vm"] != vm_name:
            what = "This device" if member["id"] == device_id else f"{member['adresse']} (same IOMMU group)"
            raise PassthroughError(f"{what} is already given to VM '{member['vm']}'", 409)
        if member["vm"] == vm_name:
            raise PassthroughError(f"{member['adresse']} is already given to this VM", 409)
    return [(m, _pci_hostdev_xml(m["id"])) for m in endpoints]


def attached(domain):
    """The host devices in a VM's persistent definition: [{"id", "type", "cle", "xml"}]."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    out = []
    for hostdev in root.findall("./devices/hostdev"):
        keys = _hostdev_keys(hostdev)
        if not keys:
            continue
        out.append({"type": hostdev.get("type"), "cles": keys, "xml": ET.tostring(hostdev, encoding="unicode")})
    return out


def device_keys(dev):
    return [dev["id"]] if dev["type"] == "pci" else _usb_keys(dev)
