"""A VM's devices, changed in its definition: disks, network interfaces with their VLANs, host devices (PCI and USB
passthrough). hyperlite-daemon runs these on this node (app/daemon/ops/vm.py, docs/design/privilege-separation.md,
section 13); the routes (app/routers/vms/devices.py) keep who may ask, the audit log and their answers.

Each device's definition is written here with ElementTree from checked fields: a volume found in a pool, a network
that exists, a MAC address libvirt or network_alloc chose. A refusal or a failure is a DeviceError, whose kind is the
daemon's (app/daemon/__init__.py), its sentence the one the routes always gave."""

import logging
import xml.etree.ElementTree as ET

import libvirt

from app.core import bridge_vlan, disk_images, network_edit, passthrough
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import get_disk_paths_in_use, lookup_pool, lookup_volume

logger = logging.getLogger(__name__)

# Default buses for older guests; SATA guests inherit their existing disk bus.
DEV_BUS_PREFIXES = {"sd": "scsi", "vd": "virtio", "hd": "ide"}


class DeviceError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _flags(domain):
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    return flags


def _xml(el):
    return ET.tostring(el, encoding="unicode")


# --- Disks ---


def attach_disk(conn, domain, pool_name, volume_name, target_dev):
    """A volume of a pool attached as `target_dev`, with the format it really has, once it names no other file to
    read (app/core/disk_images.py): QEMU reads what the definition says it is."""
    bus = DEV_BUS_PREFIXES.get(target_dev[:2], "virtio")
    if target_dev.startswith("sd"):
        root = ET.fromstring(domain.XMLDesc(0))
        if any(t.get("bus") == "sata" for t in root.findall("./devices/disk[@device='disk']/target")):
            bus = "sata"
            if domain.isActive():
                raise DeviceError("in_use", "Shut down the VM before adding a SATA disk")
    try:
        vol = lookup_volume(lookup_pool(conn, pool_name), volume_name)
    except libvirt.libvirtError:
        raise DeviceError("not_found", f"Volume '{volume_name}' not found in pool '{pool_name}'") from None
    path = vol.path()
    if path in get_disk_paths_in_use(conn):
        raise DeviceError("in_use", f"Volume '{volume_name}' is already used by a VM")
    kind = vol.info()[0]
    if kind == libvirt.VIR_STORAGE_VOL_BLOCK:
        disk_type, source, fmt = "block", "dev", "raw"  # a logical volume, a LUN, a zvol
    elif kind == libvirt.VIR_STORAGE_VOL_FILE:
        try:
            fmt = disk_images.check(path)
        except disk_images.UnsafeImage as e:
            raise DeviceError("refused", str(e)) from None
        if fmt not in ("qcow2", "raw"):
            raise DeviceError("refused", f"Volume '{volume_name}' is a {fmt} image: convert it to qcow2 to attach it")
        disk_type, source = "file", "file"
    else:
        raise DeviceError("refused", f"Volume '{volume_name}' is neither a file nor a device")
    disk = ET.Element("disk", type=disk_type, device="disk")
    ET.SubElement(disk, "driver", name="qemu", type=fmt)
    ET.SubElement(disk, "source", {source: path})
    ET.SubElement(disk, "target", dev=target_dev, bus=bus)
    try:
        domain.attachDeviceFlags(_xml(disk), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Disk attach error: {describe_exception(e)}") from e


def detach_disk(domain, target_dev):
    root = ET.fromstring(domain.XMLDesc(0))
    disk = next(
        (
            d
            for d in root.findall(".//devices/disk")
            if d.find("target") is not None and d.find("target").get("dev") == target_dev
        ),
        None,
    )
    if disk is None:
        raise DeviceError("not_found", f"Disk '{target_dev}' not found on VM '{domain.name()}'")
    try:
        domain.detachDeviceFlags(_xml(disk), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Detach error: {describe_exception(e)}") from e


# --- Network interfaces ---


def lookup_network(conn, network, vlan_tag, vlan_trunks=None):
    """How the network a VM interface goes on takes VLANs (network_edit.vlan_mode): not_found when it is missing,
    refused when it cannot take the VLANs asked for."""
    try:
        net = conn.networkLookupByName(network)
    except libvirt.libvirtError:
        raise DeviceError("not_found", f"Network '{network}' not found") from None
    # Without a VLAN asked for, how the network takes one does not matter (an interface's old VLANs are forgotten).
    mode = network_edit.vlan_mode(ET.fromstring(net.XMLDesc(0))) if vlan_tag is not None or vlan_trunks else None
    if (vlan_tag is not None or vlan_trunks) and mode is None:
        raise DeviceError("refused", network_edit.VLAN_UNSUPPORTED.format(name=network))
    if vlan_trunks:
        if mode != "bridge":
            raise DeviceError("refused", f"Network '{network}': trunk VLANs are set on a VLAN-aware Linux bridge only")
        try:
            bridge_vlan.parse_trunks(vlan_trunks)
        except bridge_vlan.VlanError as e:
            raise DeviceError("refused", str(e)) from None
    return mode


def _set_vlans(domain, mac, mode, tag, trunks):
    """Record the interface's VLANs in the VM's metadata when its network is a VLAN-aware Linux bridge (forgotten
    otherwise), and set them on its port at once on a running VM. A port that could not be set is left with no
    traffic: a failure."""
    try:
        if mode == "bridge":
            bridge_vlan.set_port(domain, mac, tag, trunks)
        elif mac.lower() in bridge_vlan.ports_of_domain_xml(ET.fromstring(domain.XMLDesc(0))):
            bridge_vlan.set_port(domain, mac, None, None)
        if mode == "bridge" and domain.isActive():
            errors = bridge_vlan.apply_vm(domain.name())
            if errors:
                raise bridge_vlan.VlanError("; ".join(errors))
    except (bridge_vlan.VlanError, libvirt.libvirtError) as e:
        raise DeviceError("failed", f"VLAN error: {describe_exception(e)}") from e


def set_interface(conn, domain, mac, network, vlan_tag, vlan_trunks):
    """The interface (by its MAC address; the first one without) moved to `network` in place, so it keeps its MAC
    address, with its VLANs."""
    mode = lookup_network(conn, network, vlan_tag, vlan_trunks)
    interfaces = ET.fromstring(domain.XMLDesc(0)).findall(".//devices/interface")
    if mac is not None:
        wanted = mac.strip().lower()
        interfaces = [
            i for i in interfaces if i.find("mac") is not None and i.find("mac").get("address", "").lower() == wanted
        ]
    if not interfaces:
        raise DeviceError(
            "not_found",
            f"No network interface with the MAC address {mac} on this VM"
            if mac
            else "No network interface found on this VM",
        )
    iface = interfaces[0]
    source = iface.find("source")
    if source is None:
        source = ET.SubElement(iface, "source")
    source.attrib.clear()
    source.set("network", network)
    vlan_el = iface.find("vlan")
    if vlan_el is not None:
        iface.remove(vlan_el)
    if vlan_tag is not None and mode == "native":
        ET.SubElement(ET.SubElement(iface, "vlan"), "tag", {"id": str(vlan_tag)})
    iface_mac = iface.find("mac").get("address") if iface.find("mac") is not None else None
    try:
        domain.updateDeviceFlags(_xml(iface), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Network update error: {describe_exception(e)}") from e
    if iface_mac:
        _set_vlans(domain, iface_mac, mode, vlan_tag, vlan_trunks)


def attach_interface(conn, domain, network, vlan_tag, vlan_trunks):
    """A new interface on `network`, of the model the VM's interfaces already use."""
    from app.core.network_alloc import generate_mac

    mode = lookup_network(conn, network, vlan_tag, vlan_trunks)
    # On a VLAN-aware bridge, chosen here rather than by libvirt: the interface's VLANs are recorded by its MAC.
    new_mac = generate_mac(conn) if mode == "bridge" else None
    model = ET.fromstring(domain.XMLDesc(0)).find("./devices/interface/model")
    iface = ET.Element("interface", type="network")
    if new_mac:
        ET.SubElement(iface, "mac", address=new_mac)
    ET.SubElement(iface, "source", network=network)
    if vlan_tag is not None and mode == "native":
        ET.SubElement(ET.SubElement(iface, "vlan"), "tag", {"id": str(vlan_tag)})
    ET.SubElement(iface, "model", type="e1000e" if model is not None and model.get("type") == "e1000e" else "virtio")
    try:
        domain.attachDeviceFlags(_xml(iface), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Interface attach error: {describe_exception(e)}") from e
    if mode == "bridge":
        _set_vlans(domain, new_mac, mode, vlan_tag, vlan_trunks)


def detach_interface(domain, mac):
    """Never the VM's last interface."""
    root = ET.fromstring(domain.XMLDesc(0))
    interfaces = root.findall(".//devices/interface")
    if len(interfaces) <= 1:
        raise DeviceError("refused", "Cannot detach the last network interface of a VM")
    iface = next(
        (
            i
            for i in interfaces
            if i.find("mac") is not None and i.find("mac").get("address", "").lower() == mac.lower()
        ),
        None,
    )
    if iface is None:
        raise DeviceError("not_found", f"Interface '{mac}' not found on VM '{domain.name()}'")
    try:
        domain.detachDeviceFlags(_xml(iface), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Detach error: {describe_exception(e)}") from e
    try:
        if mac.lower() in bridge_vlan.ports_of_domain_xml(root):
            bridge_vlan.set_port(domain, mac, None, None)
    except libvirt.libvirtError:
        logger.warning(
            "VLANs of the detached interface %s of %s left in its metadata", mac, domain.name(), exc_info=True
        )


# --- Host devices (PCI and USB passthrough, app/core/passthrough.py) ---


def hostdev_entries(conn, domain):
    """The VM's host devices, described from the host inventory, each with its <hostdev> XML (for a detach)."""
    inventory = passthrough.list_devices(conn)
    by_key = {}
    for dev in inventory["pci"] + inventory["usb"]:
        for key in passthrough.device_keys(dev):
            by_key[key] = dev
    out = []
    for item in passthrough.attached(domain):
        dev = next((by_key[k] for k in item["keys"] if k in by_key), None)
        out.append(
            {
                "id": dev["id"] if dev else item["keys"][0],
                "type": item["type"],
                "address": dev["address"] if dev else None,
                "vendor": dev["vendor"] if dev else None,
                "product": dev["product"] if dev else None,
                # A USB device can be unplugged; a PCI one can vanish after a hardware change.
                "present": dev is not None,
                "xml": item["xml"],
            }
        )
    return out


def attach_hostdev(conn, domain, device_id, confirm):
    """A host device (a PCI device with its IOMMU group, or a USB device) given to the VM, whole or not at all.
    Returns what was added: their ids and addresses."""
    running = bool(domain.isActive())
    try:
        plan = passthrough.plan_attach(conn, domain.name(), device_id, running)
    except passthrough.PassthroughError as e:
        raise DeviceError({404: "not_found", 409: "in_use"}.get(e.status, "refused"), e.message) from e
    if plan[0][0]["type"] == "pci" and not confirm:
        raise DeviceError(
            "refused",
            "Giving a PCI device to a VM takes it away from the host while the VM runs: confirm with confirm",
        )
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG | (libvirt.VIR_DOMAIN_AFFECT_LIVE if running else 0)
    done = []
    try:
        for _dev, xml in plan:
            domain.attachDeviceFlags(xml, flags)
            done.append(xml)
    except libvirt.libvirtError as e:
        for xml in done:
            try:
                domain.detachDeviceFlags(xml, flags)
            except libvirt.libvirtError:
                logger.warning("Could not roll back %s on %s", xml, domain.name(), exc_info=True)
        raise DeviceError("failed", f"Device attach error: {describe_exception(e)}") from e
    return {"added": [d["id"] for d, _ in plan], "addresses": [d["address"] for d, _ in plan]}


def detach_hostdev(conn, domain, device_id):
    item = next((e for e in hostdev_entries(conn, domain) if e["id"] == device_id), None)
    if item is None:
        raise DeviceError("not_found", "This device is not given to this VM")
    running = bool(domain.isActive())
    if item["type"] == "pci" and running:
        raise DeviceError("in_use", "Shut down the VM before taking a PCI device back")
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG | (libvirt.VIR_DOMAIN_AFFECT_LIVE if running else 0)
    try:
        domain.detachDeviceFlags(item["xml"], flags)
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Device detach error: {describe_exception(e)}") from e


# --- CD-ROM drives ---


def set_cdrom(domain, iso_name, target_dev=None):
    """An ISO of this node's library (app/core/disk_library.py) put in a CD-ROM drive: the one at `target_dev` (an
    IDE slot name; the matching SATA slot on a UEFI VM, firmware.cdrom_target), else the first; a new drive on a
    stopped VM when there is none there."""
    from pathlib import Path

    from app.core import disk_library, firmware
    from app.core.safe_paths import safe_child

    iso_filename = Path(iso_name).name
    if not iso_filename.lower().endswith(".iso"):
        raise DeviceError("refused", "Invalid ISO name")
    iso_path = safe_child(disk_library.ISOS, iso_filename)
    if not iso_path.exists():
        raise DeviceError("not_found", f"ISO '{iso_filename}' not found")
    root = ET.fromstring(domain.XMLDesc(0))
    slot_dev, slot_bus = firmware.cdrom_target(target_dev or "hdc", firmware.of_domain(root))
    cdrom = None
    for disk in root.findall("./devices/disk"):
        target = disk.find("target")
        if target_dev and target is not None and target.get("dev") == slot_dev:
            if disk.get("device") != "cdrom":
                raise DeviceError("in_use", "This target is already used by a disk")
            cdrom = disk
            break
        if disk.get("device") == "cdrom" and target_dev is None:
            cdrom = disk
            break
    try:
        if cdrom is not None:
            source = cdrom.find("source")
            if source is None:
                source = ET.SubElement(cdrom, "source")
            source.set("file", str(iso_path))
            domain.updateDeviceFlags(_xml(cdrom), _flags(domain))
            return iso_filename
        # IDE and SATA drives cannot be hot-plugged. Existing drives can still change media live, including while
        # Windows Setup is running.
        if domain.isActive():
            raise DeviceError("in_use", "Shut down the VM before adding a CD drive")
        if any(target.get("dev") == slot_dev for target in root.findall("./devices/disk/target")):
            raise DeviceError("in_use", "This target is already used by a disk")
        disk = ET.Element("disk", type="file", device="cdrom")
        ET.SubElement(disk, "driver", name="qemu", type="raw")
        ET.SubElement(disk, "source", file=str(iso_path))
        ET.SubElement(disk, "target", dev=slot_dev, bus=slot_bus)
        ET.SubElement(disk, "readonly")
        domain.attachDeviceFlags(_xml(disk), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Mount failed: {e}") from e
    return iso_filename


def eject_cdrom(domain, target_dev=None):
    """The medium of a CD-ROM drive (the one at `target_dev`, else the first) taken out; the drive stays."""
    from app.core import firmware

    root = ET.fromstring(domain.XMLDesc(0))
    slot_dev = firmware.cdrom_target(target_dev, firmware.of_domain(root))[0] if target_dev else None
    cdrom = next(
        (
            d
            for d in root.findall("./devices/disk")
            if d.get("device") == "cdrom"
            and (slot_dev is None or (d.find("target") is not None and d.find("target").get("dev") == slot_dev))
        ),
        None,
    )
    if cdrom is None:
        raise DeviceError("not_found", "No CD drive on this VM")
    source = cdrom.find("source")
    if source is not None:
        cdrom.remove(source)
    try:
        domain.updateDeviceFlags(_xml(cdrom), _flags(domain))
    except libvirt.libvirtError as e:
        raise DeviceError("failed", f"Eject failed: {e}") from e
