"""Changes made to a running VM's definition that only its next start applies.

libvirt keeps two definitions of a running domain: the live one (what QEMU runs now) and the persistent one (what
the next start uses). A setting changed "for the next start" (vCPUs, maximum memory, machine type, disk cache,
a device added or removed with the VM running without hot-plug...) only shows in the second one. Comparing a
chosen set of fields, rather than the whole XML, keeps out what libvirt fills in at runtime (aliases, PCI addresses,
ports, the current balloon size).

A reboot from inside the guest keeps the same QEMU process: the changes apply only after a stop and a start.
"""

import xml.etree.ElementTree as ET

import libvirt


def _attrs(el, *names):
    if el is None:
        return None
    parts = [f"{n}={el.get(n)}" for n in names if el.get(n)]
    return ", ".join(parts) or None


def _mib(el):
    if el is None or not (el.text or "").strip():
        return None
    value, unit = int(el.text.strip()), el.get("unit", "KiB")
    factor = {
        "b": 1 / 1048576,
        "bytes": 1 / 1048576,
        "KiB": 1 / 1024,
        "k": 1 / 1024,
        "MiB": 1,
        "M": 1,
        "GiB": 1024,
        "G": 1024,
    }
    return f"{round(value * factor.get(unit, 1 / 1024))} MiB"


def _disk(el):
    source = el.find("source")
    path = None
    if source is not None:
        path = source.get("file") or source.get("dev") or source.get("volume") or source.get("name")
    driver = _attrs(el.find("driver"), "type", "cache", "io", "discard", "iothread")
    target = el.find("target")
    bus = target.get("bus") if target is not None else None
    return " · ".join(p for p in (path or "(empty)", bus, driver) if p)


def _iface(el):
    source = el.find("source")
    src = None
    if source is not None:
        src = source.get("network") or source.get("bridge") or source.get("dev")
    model = el.find("model")
    tags = [t.get("id") for t in el.findall("vlan/tag")]
    parts = [src, model.get("type") if model is not None else None, f"VLAN {','.join(tags)}" if tags else None]
    return " · ".join(p for p in parts if p)


def _hostdev(el):
    addr = el.find("source/address")
    vendor, product = el.find("source/vendor"), el.find("source/product")
    if vendor is not None and product is not None:
        return f"usb {vendor.get('id')}:{product.get('id')}"
    if addr is not None:
        return f"{el.get('type')} " + ":".join(
            addr.get(k, "") for k in ("domain", "bus", "slot", "function") if addr.get(k)
        )
    return el.get("type") or "hostdev"


def _boot_order(root):
    items = []
    for dev in root.findall("devices/*"):
        boot = dev.find("boot")
        if boot is not None and boot.get("order"):
            if dev.tag == "disk":
                ident = dev.find("target").get("dev") if dev.find("target") is not None else "disk"
            elif dev.tag == "interface":
                ident = dev.find("mac").get("address") if dev.find("mac") is not None else "interface"
            else:
                ident = dev.tag
            items.append((int(boot.get("order")), ident))
    if items:
        return " > ".join(i for _, i in sorted(items))
    legacy = [b.get("dev") for b in root.findall("os/boot")]
    return " > ".join(legacy) or None


def _fields(xml):
    root = ET.fromstring(xml)
    os_type = root.find("os/type")
    cpu = root.find("cpu")
    cpu_desc = None
    if cpu is not None:
        topo = _attrs(cpu.find("topology"), "sockets", "dies", "cores", "threads")
        cpu_desc = " · ".join(p for p in (cpu.get("mode"), (cpu.findtext("model") or "").strip() or None, topo) if p)
    loader = root.find("os/loader")
    firmware = (root.find("os").get("firmware") if root.find("os") is not None else None) or (
        "efi" if loader is not None and loader.get("type") == "pflash" else "bios"
    )
    vcpu = root.find("vcpu")
    fields = {
        ("vcpu", None): (vcpu.text or "").strip() if vcpu is not None else None,
        ("memoire", None): _mib(root.find("memory")),
        ("machine", None): os_type.get("machine") if os_type is not None else None,
        ("cpu", None): cpu_desc,
        ("firmware", None): firmware,
        ("demarrage", None): _boot_order(root),
        ("ballon", None): (
            root.find("devices/memballoon").get("model") if root.find("devices/memballoon") is not None else None
        ),
        ("graphique", None): ", ".join(g.get("type") for g in root.findall("devices/graphics")) or None,
    }
    for disk in root.findall("devices/disk"):
        target = disk.find("target")
        if target is not None and target.get("dev"):
            fields[("disque", target.get("dev"))] = _disk(disk)
    for iface in root.findall("devices/interface"):
        mac = iface.find("mac")
        if mac is not None and mac.get("address"):
            fields[("interface", mac.get("address").lower())] = _iface(iface)
    for dev in root.findall("devices/hostdev"):
        desc = _hostdev(dev)
        fields[("passthrough", desc)] = desc
    return fields


def diff(live_xml, next_xml):
    """Fields whose value at the next start differs from the running one; `actuel` or `prochain` is None for a
    device only in one of the two."""
    live, nxt = _fields(live_xml), _fields(next_xml)
    changes = []
    for key in list(live) + [k for k in nxt if k not in live]:
        now, after = live.get(key), nxt.get(key)
        if now != after:
            changes.append({"cle": key[0], "objet": key[1], "actuel": now, "prochain": after})
    return changes


def pending_changes(domain):
    if not domain.isActive() or not domain.isPersistent():
        return []
    return diff(domain.XMLDesc(0), domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
