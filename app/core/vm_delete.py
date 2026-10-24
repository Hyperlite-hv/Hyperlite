"""A VM deleted (DELETE /vms/{name}, app/routers/vms/lifecycle.py, and the automatic clean-up of inactive VMs,
app/core/vm_cleanup.py): its definition, its disks, the drives Hyperlite generated for it and its DHCP reservations.
hyperlite-daemon deletes a VM of this node (vm.delete, app/daemon/ops/vm.py, docs/design/privilege-separation.md,
section 13); another node's keeps the API's path over SSH until the cluster domain. The API keeps the confirmation, the
lock, the task, and forgets what it records of the VM (app/core/vm_records.py).

Which disks go is Proxmox VE's rule rather than vSphere's: vSphere's "Delete from disk" deletes every disk attached to
the VM, a disk another VM shares included; Proxmox VE deletes a VM's own volumes only, on a storage it manages, and
never the base of a linked clone. A disk is therefore kept when it is outside every storage pool of the node (a file
or a device attached by hand: Hyperlite did not make it), when another VM uses it, or when another VM's disk reads
through it. What is kept is said, with its reason, as vSphere's and Proxmox VE's tasks do."""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import libvirt

from app.core import firmware, linked_disks, lvm_storage, vm_records, zfs_storage
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import pool_for_path, refresh_pools_for_paths
from app.core.vm_builder import IMAGES_DIR, generated_drives

logger = logging.getLogger(__name__)


class DeleteError(Exception):
    """A refusal or a failure, with the daemon's kind and a sentence for the user."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class Leftovers:
    """What goes with a VM, read from its definition before the undefine, which leaves nothing to read."""

    files: list[Path] = field(default_factory=list)
    # Block disks (ZFS zvols, LVM logical volumes, iSCSI LUNs): source='dev', deleted by `zfs destroy` or libvirt,
    # never unlinked.
    devices: list[str] = field(default_factory=list)
    reservations: list = field(default_factory=list)
    undefine_flags: int = 0


def leftovers(domain, name):
    """ALL the disks and ALL the interfaces, not only the first ones: a multi-disk or multi-NIC VM must not leave an
    orphan qcow2 file or a phantom DHCP reservation behind (the secondary disk of a deleted VM once stayed and made
    the next clone of that name fail). The drives Hyperlite generated for THIS VM (cloud-init, and the
    Kickstart/autoinstall answers of VMs created before 1.0) go with it, wherever its pool put them, and never an image
    of the ISO library that carries the same name (vm_builder.generated_drives). An unreadable definition leaves
    nothing to remove but the definition."""
    found = Leftovers()
    try:
        root = ET.fromstring(domain.XMLDesc())
    except (libvirt.libvirtError, ET.ParseError):
        logger.warning("Definition of the deleted VM %s unreadable: its disks are left in place", name, exc_info=True)
        return found
    found.undefine_flags = firmware.undefine_flags(root)
    own_drives = {path for _disk, path in generated_drives(root, name)}
    for disk in root.findall(".//devices/disk"):
        source = disk.find("source")
        if source is None:
            continue
        if disk.get("type") == "block":
            if source.get("dev") and disk.get("device") == "disk":
                found.devices.append(source.get("dev"))
            continue
        path = source.get("file")
        if path and (disk.get("device") == "disk" or Path(path) in own_drives):
            found.files.append(Path(path))
    found.reservations = vm_records.reservations(root)
    return found


def undefine(domain, found):
    """VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA: without it, undefine fails as soon as a snapshot remains ("cannot delete
    inactive domain with N snapshots"), even a partially deleted one; the qcow2 file that held the internal snapshots
    goes right after. A UEFI VM takes its NVRAM and TPM state with it (app/core/firmware.py)."""
    domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA | found.undefine_flags)


def delete_logical_volumes(conn, found, name, node=None):
    """A logical volume of an LVM pool goes with its VM (a thick one through libvirt, a thin one and its snapshots
    through lvm); an iSCSI LUN or a volume outside such a pool is not Hyperlite's to delete."""
    for dev in found.devices:
        if dev.startswith("/dev/zvol/"):
            continue
        try:
            lvm_storage.delete_vm_volume(conn, dev, node)
        except (libvirt.libvirtError, lvm_storage.LvmError):
            logger.warning("Logical volume %s of the deleted VM %s left in place", dev, name, exc_info=True)


def _other_disks(conn, name):
    """(VM, source, whether a file) of every disk of the node's other VMs, running or not."""
    found = []
    for domain in conn.listAllDomains():
        try:
            if domain.name() == name:
                continue
            root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except (libvirt.libvirtError, ET.ParseError):
            logger.warning("Definition of a VM unreadable while the disks of %s were sorted", name, exc_info=True)
            continue
        for source in root.findall("./devices/disk/source"):
            path = source.get("file") or source.get("dev")
            if path:
                found.append((domain.name(), path, bool(source.get("file"))))
    return found


def kept(conn, name, found, local=True):
    """{path: reason} for the disks of `found` that do not go with the VM `name` (the module's rule). `local`: the
    files are on this host, whose qcow2 headers say which disks read through which; another node's are not read."""
    others = _other_disks(conn, name)
    files = [str(path) for path in found.files]
    reasons = {}
    for path in [*files, *found.devices]:
        users = sorted({vm for vm, other, _file in others if other == path})
        if users:
            reasons[path] = f"used by VM {', '.join(users)}"
            continue
        if path not in files:
            continue  # a device: zfs_storage and lvm_storage delete only a zvol or a logical volume of their pools
        readers = sorted(
            {vm for vm, other, is_file in others if local and is_file and linked_disks.depends_on(other, path)}
        )
        if readers:
            reasons[path] = f"base of the linked disks of VM {', '.join(readers)}"
        elif Path(path).parent != IMAGES_DIR and pool_for_path(conn, path) is None:
            reasons[path] = "in no storage pool"
    return reasons


def delete(conn, name):
    """Delete the stopped VM `name` of this node and what goes with it (leftovers, but what kept() keeps). A disk
    already gone, or a zvol or a logical volume removed by hand, does not make the deletion fail. Returns {path: reason}
    of the disks kept."""
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        raise DeleteError("not_found", f"VM '{name}' not found") from None
    # Checked again here, whatever the API checked: a running VM's disks are in use.
    if domain.isActive():
        raise DeleteError("in_use", f"VM '{name}' is running. Stop it before deleting it")
    found = leftovers(domain, name)
    keep = kept(conn, name, found)
    try:
        undefine(domain, found)
    except libvirt.libvirtError as e:
        raise DeleteError("failed", f"Unable to delete the VM: {describe_exception(e)}") from e
    vm_records.release_addresses(conn, found.reservations)
    found.files = [path for path in found.files if str(path) not in keep]
    found.devices = [dev for dev in found.devices if dev not in keep]
    for path in keep:
        logger.warning("Disk %s of the deleted VM %s kept: %s", path, name, keep[path])
    for path in found.files:
        path.unlink(missing_ok=True)
    # Removed behind libvirt's back: without a refresh their pools kept listing them.
    refresh_pools_for_paths(conn, found.files)
    for dev in found.devices:
        # '/dev/zvol/<pool>/<name>': a zvol's name never holds a '/' itself (validate_zfs_name).
        parts = dev.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["dev", "zvol"]:
            try:
                zfs_storage.delete_zvol(parts[2], "/".join(parts[3:]))
            except zfs_storage.ZfsError:
                logger.warning("Zvol %s of the deleted VM %s left in place", dev, name, exc_info=True)
    delete_logical_volumes(conn, found, name)
    return keep
