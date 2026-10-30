"""A VM cloned on this node (POST /vms/{name}/clone, app/routers/vms/clone.py): every disk copied into the images
directory, a new name, UUID, NVRAM and MAC addresses with their DHCP reservations, and a new cloud-init instance when
the source was set up by cloud-init. hyperlite-daemon runs it (vm.clone, app/daemon/ops/vm.py,
docs/design/privilege-separation.md, section 13): the source's disks are read and the copies written as root. The API
keeps who may ask, the locks, the task and what it records.

What a clone fixed over time:
- every disk is copied, not only the first: a multi-disk clone once shared the source's other disks, two VMs then
  writing the same qcow2 file;
- a clone of a VM set up by cloud-init (its definition has its <name>-cloudinit.iso drive) gets a new instance id and
  host name, so that cloud-init regenerates its SSH host keys at first boot; not its password, which Hyperlite never
  keeps. A VM installed from an ISO cannot be customised, as on a hypervisor without a guest agent: the answer says
  so (`guest_customization`)."""

import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import bridge_vlan, disk_space, firmware, iscsi
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import refresh_pools_for_paths
from app.core.network_alloc import allocate_static_ip, generate_mac, release_static_ip
from app.core.safe_paths import safe_child
from app.core.vm_builder import IMAGES_DIR, cloudinit_drive, create_cloudinit_reseed_iso, validate_name

logger = logging.getLogger(__name__)


class CloneError(Exception):
    """A refusal or a failure, with the daemon's kind (in_use: the 409 the route always answered)."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def check(conn, name, new_name):
    """Every refusal a clone can meet before its first byte is copied: (the source's parsed definition, its disk
    elements)."""
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        raise CloneError("not_found", f"VM '{name}' not found") from None
    if iscsi.iscsi_disks_of_domain(domain):
        raise CloneError("refused", "Cloning is not available yet for a VM whose disks are iSCSI LUNs")
    name_error = validate_name(new_name)
    if name_error:
        raise CloneError("refused", name_error)
    try:
        conn.lookupByName(new_name)
    except libvirt.libvirtError:
        pass
    else:
        raise CloneError("in_use", f"A VM '{new_name}' already exists")
    if domain.isActive():
        raise CloneError("in_use", "Stop the VM before cloning it")

    root = ET.fromstring(domain.XMLDesc(0))
    disks = root.findall("./devices/disk[@device='disk']")
    if not disks:
        raise CloneError("failed", "Source disk not found")
    sources = [disk.find("source").get("file") for disk in disks if disk.find("source") is not None]
    if None in sources or len(sources) != len(disks):
        raise CloneError("failed", "Source disk path not found")
    for i in range(len(disks)):
        target = _disk_path(new_name, i)
        if target.exists():
            raise CloneError("in_use", f"A disk file '{target.name}' already exists")
    # Every copy lands in the images directory, on the system disk by default: refused before the first byte when it
    # would not fit (app/core/disk_space.py), rather than filling it hours later.
    try:
        disk_space.ensure_room(IMAGES_DIR, sum(disk_space.allocated_bytes(p) for p in sources), "this clone")
    except disk_space.NotEnoughSpace as e:
        raise CloneError("in_use", str(e)) from None
    return root, disks


def _disk_path(new_name, index):
    return safe_child(IMAGES_DIR, f"{new_name}{'' if index == 0 else f'-{index + 1}'}.qcow2")


def _undo(conn, disk_paths, reseed_iso, reservations):
    """What a failed clone made: its disks, its cloud-init drive and its address reservations."""
    for path in disk_paths:
        Path(path).unlink(missing_ok=True)
    if reseed_iso:
        Path(reseed_iso).unlink(missing_ok=True)
    for network, mac in reservations:
        try:
            release_static_ip(conn, network, mac)
        except libvirt.libvirtError:
            logger.warning("Reservation of %s on %s left after a failed clone", mac, network, exc_info=True)


def clone(conn, name, new_name, check_only=False):
    """The stopped VM `name` copied as `new_name`: {"clone", "guest_customization"}; check_only: the checks alone,
    None. Nothing the clone made is left behind when it fails."""
    root, disks = check(conn, name, new_name)
    if check_only:
        return None
    # Read before the CD-ROM drives are dropped from the copy's definition below.
    source_has_cloudinit = cloudinit_drive(root, name) is not None
    disk_paths, reseed_iso, reservations = [], None, []
    try:
        for i, disk in enumerate(disks):
            source = disk.find("source")
            target = _disk_path(new_name, i)
            # Listed before the copy: a copy that stopped on a full disk left its file there, filling it.
            disk_paths.append(str(target))
            try:
                subprocess.run(
                    ["qemu-img", "convert", "-O", "qcow2", source.get("file"), str(target)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                logger.warning("Copy of %s for the clone %s failed: %s", source.get("file"), new_name, e.stderr or e)
                raise CloneError("failed", "Disk copy failed") from e
            source.set("file", str(target))
            # The copy is qcow2 whatever the source's format: a raw source (a VM defined before Hyperlite managed the
            # node) kept its raw driver, and the clone read the qcow2 file as a raw disk.
            driver = disk.find("driver")
            if driver is None:
                driver = ET.SubElement(disk, "driver", {"name": "qemu"})
            driver.set("type", "qcow2")

        root.find("name").text = new_name
        uuid = root.find("uuid")
        if uuid is not None:
            root.remove(uuid)  # libvirt generates a new, distinct one at defineXML
        # A UEFI source: the clone gets its own NVRAM (and, with its new UUID, its own TPM state).
        firmware.drop_nvram(root)
        devices = root.find("devices")
        for cdrom in devices.findall("disk[@device='cdrom']"):
            devices.remove(cdrom)

        # An explicit MAC address, rather than one libvirt picks at random, so that the clone's fixed address is
        # reserved at once, as at a creation (app/core/network_alloc.py). A failed reservation costs the fixed
        # address only.
        for iface in devices.findall("interface"):
            old_mac = iface.find("mac")
            if old_mac is not None:
                iface.remove(old_mac)
            new_mac = generate_mac(conn)
            ET.SubElement(iface, "mac", {"address": new_mac})
            if old_mac is not None and old_mac.get("address"):
                # VLANs on a VLAN-aware bridge are recorded by MAC address: the clone's interface keeps them.
                bridge_vlan.remap_mac(root, old_mac.get("address"), new_mac)
            network = iface.find("source").get("network") if iface.find("source") is not None else None
            if network:
                try:
                    allocate_static_ip(conn, network, new_mac)
                    reservations.append((network, new_mac))
                except libvirt.libvirtError:
                    logger.warning("No fixed address for %s of the clone %s", new_mac, new_name, exc_info=True)

        if source_has_cloudinit:
            try:
                reseed_iso = create_cloudinit_reseed_iso(new_name)
            except subprocess.CalledProcessError:
                logger.warning("No cloud-init drive for the clone %s: not customised", new_name, exc_info=True)
            if reseed_iso:
                cdrom = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
                ET.SubElement(cdrom, "driver", {"name": "qemu", "type": "raw"})
                ET.SubElement(cdrom, "source", {"file": str(reseed_iso)})
                dev, bus = firmware.cdrom_target("hdc", firmware.of_domain(root))
                ET.SubElement(cdrom, "target", {"dev": dev, "bus": bus})
                ET.SubElement(cdrom, "readonly")

        try:
            made = conn.defineXML(ET.tostring(root, encoding="unicode"))
        except libvirt.libvirtError as e:
            raise CloneError("failed", f"Clone definition failed: {describe_exception(e)}") from e
    except BaseException:
        _undo(conn, disk_paths, reseed_iso, reservations)
        raise
    # From here its disks are the clone's.
    refresh_pools_for_paths(conn, [*disk_paths, reseed_iso])
    return {"clone": made.name(), "guest_customization": reseed_iso is not None}
