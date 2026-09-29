"""Advanced hardware settings of a VM: per-disk cache, discard, I/O mode, I/O thread and throughput limits; the boot
order; memory ballooning; the machine type.

What applies when:
- throughput limits (IOPS, MB/s) are applied live to a running VM, and kept in its definition;
- cache, discard, I/O mode and the I/O thread change how QEMU opens the disk: written to the definition, they apply
  at the VM's next start;
- the boot order is read by the firmware at boot;
- ballooning: the balloon device is part of the definition (next start); the minimum memory the guest can be
  squeezed to applies live when the balloon is already there.
- the machine type follows the firmware (UEFI needs q35) and the guest's devices depend on it: only a newer version
  of the same family is offered, on a stopped VM. Moving between i440fx and q35 breaks an installed guest.
"""

import re
import xml.etree.ElementTree as ET

import libvirt

CACHE_MODES = ("none", "writeback", "writethrough", "directsync", "unsafe")
DISCARD_MODES = ("ignore", "unmap")
IO_MODES = ("native", "threads", "io_uring")
MAX_IOPS = 10_000_000
MAX_MBPS = 100_000


class OptionError(ValueError):
    pass


def _inactive_root(domain):
    return ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))


def _disk(root, dev):
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target is not None and target.get("dev") == dev:
            return disk
    return None


def _iotune(disk):
    tune = disk.find("iotune")
    if tune is None:
        return None, None
    iops = tune.findtext("total_iops_sec")
    bps = tune.findtext("total_bytes_sec")
    return (int(iops) if iops else None), (round(int(bps) / 1024**2) if bps else None)


def describe(domain):
    """The settings as the definition holds them (what the VM gets at its next start)."""
    root = _inactive_root(domain)
    disks = []
    boot = []
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target is None:
            continue
        driver = disk.find("driver")
        iops, mbps = _iotune(disk)
        dev = target.get("dev")
        order = disk.find("boot")
        if order is not None:
            boot.append((int(order.get("order", "99")), dev))
        disks.append(
            {
                "cible": dev,
                "type": disk.get("device"),
                "bus": target.get("bus"),
                "cache": driver.get("cache") if driver is not None else None,
                "discard": driver.get("discard") if driver is not None else None,
                "io": driver.get("io") if driver is not None else None,
                "iothread": bool(driver is not None and driver.get("iothread")),
                "iops": iops,
                "mbps": mbps,
            }
        )
    interfaces = []
    for iface in root.findall("./devices/interface"):
        order = iface.find("boot")
        mac = iface.find("mac")
        if mac is None:
            continue
        interfaces.append(f"net:{mac.get('address')}")
        if order is not None:
            boot.append((int(order.get("order", "99")), f"net:{mac.get('address')}"))
    balloon = root.find("./devices/memballoon")
    memory = int(root.findtext("memory") or 0) // 1024
    current = int(root.findtext("currentMemory") or root.findtext("memory") or 0) // 1024
    os_type = root.find("./os/type")
    return {
        "disques": disks,
        "interfaces": interfaces,
        "ordre_demarrage": [dev for _order, dev in sorted(boot)],
        "ballooning": {
            "actif": balloon is not None and balloon.get("model") not in (None, "none"),
            "memoire_mo": memory,
            "minimum_mo": current,
        },
        "machine": os_type.get("machine") if os_type is not None else None,
    }


def _validate_disk(opts):
    if opts.get("cache") not in (None, *CACHE_MODES):
        raise OptionError(f"Unknown cache mode: {opts['cache']}")
    if opts.get("discard") not in (None, *DISCARD_MODES):
        raise OptionError(f"Unknown discard mode: {opts['discard']}")
    if opts.get("io") not in (None, *IO_MODES):
        raise OptionError(f"Unknown I/O mode: {opts['io']}")
    if opts.get("io") == "native" and opts.get("cache") not in ("none", "directsync"):
        # QEMU refuses aio=native with a host page cache: the VM would not start.
        raise OptionError("The native I/O mode needs the cache mode 'none' or 'directsync'")
    for key, top in (("iops", MAX_IOPS), ("mbps", MAX_MBPS)):
        value = opts.get(key)
        if value is not None and not (0 <= int(value) <= top):
            raise OptionError(f"{key} must be between 0 and {top} (0: no limit)")


def _ensure_iothread(root):
    """At least one I/O thread declared on the VM, for the disks that ask for one."""
    el = root.find("iothreads")
    if el is None:
        el = ET.SubElement(root, "iothreads")
        el.text = "1"
    elif int(el.text or 0) < 1:
        el.text = "1"


def set_disk_options(conn, domain, dev, opts):
    """opts: cache, discard, io, iothread (bool), iops, mbps (0 or None: no limit). Returns {"a_redemarrer"}."""
    _validate_disk(opts)
    root = _inactive_root(domain)
    disk = _disk(root, dev)
    if disk is None:
        raise OptionError(f"No disk {dev} on this VM")
    if disk.get("device") == "cdrom":
        raise OptionError("A CD-ROM drive has no such options")
    driver = disk.find("driver")
    if driver is None:
        driver = ET.SubElement(disk, "driver", {"name": "qemu"})
    before = dict(driver.attrib)
    for key in ("cache", "discard", "io"):
        if opts.get(key):
            driver.set(key, opts[key])
        else:
            driver.attrib.pop(key, None)
    target = disk.find("target")
    if opts.get("iothread"):
        if target is not None and target.get("bus") != "virtio":
            raise OptionError("An I/O thread only applies to a virtio disk")
        _ensure_iothread(root)
        driver.set("iothread", "1")
    else:
        driver.attrib.pop("iothread", None)
    iops = int(opts.get("iops") or 0)
    bps = int(opts.get("mbps") or 0) * 1024**2
    tune = disk.find("iotune")
    if tune is not None:
        disk.remove(tune)
    if iops or bps:
        tune = ET.SubElement(disk, "iotune")
        if bps:
            ET.SubElement(tune, "total_bytes_sec").text = str(bps)
        if iops:
            ET.SubElement(tune, "total_iops_sec").text = str(iops)
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    restart = False
    if domain.isActive():
        domain.setBlockIoTune(dev, {"total_iops_sec": iops, "total_bytes_sec": bps}, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        restart = dict(driver.attrib) != before
    return {"a_redemarrer": restart}


def set_boot_order(conn, domain, order):
    """order: disk targets ('vda', 'sdb') and 'net:<mac>' entries, first boots first. Devices left out never boot."""
    if not order:
        raise OptionError("At least one boot device is needed")
    if len(set(order)) != len(order):
        raise OptionError("A device appears twice in the boot order")
    root = _inactive_root(domain)
    devices = {}
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target is not None:
            devices[target.get("dev")] = disk
    for iface in root.findall("./devices/interface"):
        mac = iface.find("mac")
        if mac is not None:
            devices[f"net:{mac.get('address')}"] = iface
    unknown = [d for d in order if d not in devices]
    if unknown:
        raise OptionError(f"Not a device of this VM: {', '.join(unknown)}")
    for el in devices.values():
        boot = el.find("boot")
        if boot is not None:
            el.remove(boot)
    for os_boot in root.findall("./os/boot"):
        root.find("os").remove(os_boot)  # per-device order and <os><boot> cannot be combined
    for i, dev in enumerate(order, start=1):
        ET.SubElement(devices[dev], "boot", {"order": str(i)})
    conn.defineXML(ET.tostring(root, encoding="unicode"))


def set_balloon(conn, domain, enabled, minimum_mb=None):
    """Enabled: a virtio balloon, and the guest may be squeezed down to minimum_mb. Returns {"a_redemarrer"}."""
    root = _inactive_root(domain)
    memory = int(root.findtext("memory") or 0)
    devices = root.find("devices")
    balloon = devices.find("memballoon")
    had = balloon is not None and balloon.get("model") not in (None, "none")
    if balloon is None:
        balloon = ET.SubElement(devices, "memballoon")
    balloon.set("model", "virtio" if enabled else "none")
    for child in list(balloon):
        if child.tag == "address":
            balloon.remove(child)  # libvirt assigns a new slot
    current = root.find("currentMemory")
    if current is None:
        current = ET.SubElement(root, "currentMemory", {"unit": "KiB"})
    if enabled and minimum_mb is not None:
        kib = int(minimum_mb) * 1024
        if not (256 * 1024 <= kib <= memory):
            raise OptionError(f"The minimum memory must be between 256 MB and the VM's memory ({memory // 1024} MB)")
        current.text = str(kib)
    else:
        current.text = str(memory)
    current.set("unit", "KiB")
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    restart = False
    if domain.isActive():
        if had and enabled:
            domain.setMemoryFlags(int(current.text), libvirt.VIR_DOMAIN_AFFECT_LIVE)
        elif had != enabled:
            restart = True
    return {"a_redemarrer": restart}


# Versioned machine names carry a number (pc-q35-8.2) or, on some distributions, a release name (pc-i440fx-noble):
# versions cannot be compared from names. What libvirt does say is which one the family's alias ('pc', 'q35')
# stands for today: that is the one offered.
_MACHINE_RE = re.compile(r"^pc-(q35|i440fx)-[A-Za-z0-9._-]+$")
_ALIASES = {"pc": "i440fx", "q35": "q35"}


def _family(machine):
    if machine in _ALIASES:
        return _ALIASES[machine]
    m = _MACHINE_RE.match(machine or "")
    return m.group(1) if m else None


def newer_machines(conn, machine):
    """The current default version of the VM's machine family on this host, when the VM uses an older one."""
    family = _family(machine)
    if family is None:
        return []
    caps = ET.fromstring(conn.getCapabilities())
    for guest in caps.findall("guest"):
        arch = guest.find("arch")
        if arch is None or arch.get("name") != "x86_64":
            continue
        for m in arch.findall("machine") + arch.findall("domain/machine"):
            canonical = m.get("canonical")
            if m.text in _ALIASES and _ALIASES[m.text] == family and canonical and canonical != machine:
                return [canonical]
    return []


def set_machine(conn, domain, machine):
    if domain.isActive():
        raise OptionError("Stop the VM to change its machine type")
    root = _inactive_root(domain)
    os_type = root.find("./os/type")
    current = os_type.get("machine") if os_type is not None else None
    if machine not in newer_machines(conn, current):
        raise OptionError(f"{machine} is not the current version of this VM's machine family ({current})")
    os_type.set("machine", machine)
    conn.defineXML(ET.tostring(root, encoding="unicode"))
