"""A VM's snapshots (app/routers/vms/snapshots.py), each kind on its storage's own mechanism: a qcow2 VM's are libvirt's
internal snapshots (its memory too while it runs), a VM on ZFS gets one `zfs snapshot` of all its zvols at once, a VM
on LVM-thin a thin snapshot of each logical volume. hyperlite-daemon takes, restores and deletes them
(vm.snapshot_*, app/daemon/ops/vm.py, docs/design/privilege-separation.md, section 13): `zfs`, `lvm` and the libvirt
connection that may write are root's. The API keeps who may ask, the lock, the confirmation, the task and the
replication chain the daemon's dropped checkpoints end (app/core/checkpoints.py).

Every check runs before the work, with check_only, so that the route answers its refusal at once and only then starts
the task: a refusal never arrives as a failed task after the fact."""

import logging
import xml.etree.ElementTree as ET

import libvirt

from app.core import checkpoints, firmware, iscsi, lvm_storage, zfs_storage
from app.core.error_messages import describe_exception
from app.core.vm_builder import validate_name

logger = logging.getLogger(__name__)


class SnapshotError(Exception):
    """A refusal or a failure, with the daemon's kind."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def zvol_disks(root):
    """(pool, zvol) of each ZFS zvol disk of a parsed definition. A VM created on a ZFS pool has ALL its disks as zvols
    of that pool, never mixed with qcow2 files."""
    specs = []
    for disk in root.findall(".//devices/disk[@type='block'][@device='disk']"):
        source = disk.find("source")
        parts = (source.get("dev") or "").strip("/").split("/") if source is not None else []
        if len(parts) >= 4 and parts[:2] == ["dev", "zvol"]:
            specs.append((parts[2], "/".join(parts[3:])))
    return specs


def lvm_disk_paths(root):
    """The VM's block disks shaped as logical volumes (/dev/<group>/<volume>): on an LVM pool, all of them."""
    paths = [s.get("dev", "") for s in root.findall("devices/disk[@type='block'][@device='disk']/source")]
    return [p for p in paths if lvm_storage.lv_of_path(p) is not None]


def check_existing_name(name):
    """ZFS reads `a%b` as a range of snapshots and `a,b` as a list: such a name, never made here, would destroy or roll
    back more than the one snapshot named (and audited)."""
    if not name or any(c in name for c in "%,@/") or name != name.strip():
        raise SnapshotError("refused", f"Invalid snapshot name: {name!r}")


def _domain(conn, vm):
    try:
        return conn.lookupByName(vm)
    except libvirt.libvirtError:
        raise SnapshotError("not_found", f"VM '{vm}' not found") from None


def _lvm(root):
    """The thin disks of an LVM VM, or None when it is not on LVM; thick volumes have no snapshot here."""
    paths = lvm_disk_paths(root)
    if not paths:
        return None
    disks = lvm_storage.thin_disks(paths)
    return disks or []


def _lvm_snapshot_named(disks, name):
    if not disks or name not in {s["name"] for s in lvm_storage.list_snapshots(disks)}:
        raise SnapshotError("not_found", f"Snapshot '{name}' not found")


def lvm_snapshots(conn, vm):
    """The LVM-thin snapshots of the VM's first disk (all its disks are snapshotted together); [] when it is not on
    LVM-thin. `lvm` is root's: the listing asks the daemon for this part only."""
    root = ET.fromstring(_domain(conn, vm).XMLDesc(0))
    disks = _lvm(root)
    return lvm_storage.list_snapshots(disks) if disks else []


def create(conn, vm, name, description=None, check_only=False):
    """{"checkpoints_dropped"}: a stopped qcow2 VM's replication checkpoints, which libvirt's offline snapshot refuses."""
    domain = _domain(conn, vm)
    error = validate_name(name, "snapshot")
    if error:
        raise SnapshotError("refused", error)
    if iscsi.iscsi_disks_of_domain(domain):
        raise SnapshotError("refused", "A snapshot is not available yet for a VM whose disks are iSCSI LUNs")
    root = ET.fromstring(domain.XMLDesc(0))
    zvols = zvol_disks(root)
    lvm = None if zvols else _lvm(root)
    if lvm == []:
        raise SnapshotError(
            "refused", "This VM's disks are thick LVM volumes: snapshots need an LVM pool with a thin pool (LVM-thin)"
        )
    if not zvols and lvm is None:
        # A raw block disk left (zvols and logical volumes took the branches above, iSCSI was refused). libvirt's
        # internal snapshots need qcow2: the task would fail after the fact with libvirt's own message.
        if root.find("devices/disk[@type='block'][@device='disk']") is not None:
            raise SnapshotError("refused", "This VM's disks are raw block devices: their snapshots are not available")
        # QEMU cannot save a running VM's state while its firmware is in pflash (every UEFI VM): libvirt would fail
        # the task with "internal snapshots of a VM with pflash based firmware are not supported".
        if domain.isActive() and firmware.is_pflash(root):
            raise SnapshotError(
                "in_use",
                "This VM uses UEFI firmware: its snapshots are taken while it is stopped. Shut it down, then take the "
                "snapshot",
            )
        try:
            domain.snapshotLookupByName(name)
        except libvirt.libvirtError:
            pass
        else:
            raise SnapshotError("refused", f"A snapshot '{name}' already exists for this VM")
    if check_only:
        return None

    if zvols:
        # Disk only, never the memory: one atomic command over all the zvols.
        try:
            zfs_storage.snapshot_zvols(zvols, name)
        except zfs_storage.ZfsError as e:
            raise SnapshotError("failed", e.message) from e
        return {"checkpoints_dropped": 0}
    if lvm is not None:
        _lvm_snapshot(domain, vm, lvm, name)
        return {"checkpoints_dropped": 0}
    # libvirt refuses an offline snapshot while a replication checkpoint exists.
    dropped = 0 if domain.isActive() else checkpoints.drop(domain)
    snapshot = ET.Element("domainsnapshot")
    ET.SubElement(snapshot, "name").text = name
    if description:
        ET.SubElement(snapshot, "description").text = description
    try:
        # flags=0: internal, with the memory while the VM runs, the disks only while it is stopped.
        domain.snapshotCreateXML(ET.tostring(snapshot, encoding="unicode"), 0)
    except libvirt.libvirtError as e:
        raise SnapshotError("failed", describe_exception(e)) from e
    return {"checkpoints_dropped": dropped}


def _lvm_snapshot(domain, vm, disks, name):
    """A running VM with several disks is paused meanwhile (about a second per disk), so that its disks all show the
    same instant, as ZFS's single atomic command gives."""
    paused = len(disks) > 1 and domain.isActive()
    if paused:
        domain.suspend()
    try:
        lvm_storage.snapshot(disks, name)
    except lvm_storage.LvmError as e:
        raise SnapshotError("failed", str(e)) from e
    finally:
        if paused:
            try:
                domain.resume()
            except libvirt.libvirtError:
                logger.error("VM %s left paused after its snapshot", vm, exc_info=True)


def restore(conn, vm, name, check_only=False):
    """{"checkpoints_dropped"}: a revert rewrites the disks without marking the replication bitmap, so the next copy
    is a full one. A ZFS rollback or an LVM restore needs the VM stopped: under a running QEMU, the guest's cache would
    no longer match its disk, a near-certain corruption."""
    check_existing_name(name)
    domain = _domain(conn, vm)
    root = ET.fromstring(domain.XMLDesc(0))
    zvols = zvol_disks(root)
    lvm = None if zvols else _lvm(root)
    if zvols:
        if domain.isActive():
            raise SnapshotError(
                "in_use",
                "The VM must be stopped before restoring a ZFS snapshot (unlike the qcow2 snapshot, a ZFS rollback "
                "cannot be done while running)",
            )
    elif lvm is not None:
        _lvm_snapshot_named(lvm, name)
        if domain.isActive():
            raise SnapshotError("in_use", "The VM must be stopped before restoring an LVM snapshot")
    else:
        try:
            snapshot = domain.snapshotLookupByName(name)
        except libvirt.libvirtError:
            raise SnapshotError("not_found", f"Snapshot '{name}' not found") from None
    if check_only:
        return None

    try:
        if zvols:
            zfs_storage.rollback_zvols(zvols, name)
            return {"checkpoints_dropped": 0}
        if lvm is not None:
            lvm_storage.rollback(lvm, name)
            return {"checkpoints_dropped": 0}
        dropped = checkpoints.drop(domain)
        domain.revertToSnapshot(snapshot, 0)
        return {"checkpoints_dropped": dropped}
    except zfs_storage.ZfsError as e:
        raise SnapshotError("failed", e.message) from e
    except (lvm_storage.LvmError, libvirt.libvirtError) as e:
        raise SnapshotError("failed", describe_exception(e)) from e


def delete(conn, vm, name):
    """Nearly instantaneous whatever the kind (libvirt reparents a child snapshot itself): the route waits for it."""
    check_existing_name(name)
    domain = _domain(conn, vm)
    root = ET.fromstring(domain.XMLDesc(0))
    zvols = zvol_disks(root)
    lvm = None if zvols else _lvm(root)
    try:
        if zvols:
            zfs_storage.delete_zvol_snapshot(zvols, name)
        elif lvm is not None:
            _lvm_snapshot_named(lvm, name)
            lvm_storage.delete_snapshot(lvm, name)
        else:
            try:
                snapshot = domain.snapshotLookupByName(name)
            except libvirt.libvirtError:
                raise SnapshotError("not_found", f"Snapshot '{name}' not found") from None
            snapshot.delete(0)
    except zfs_storage.ZfsError as e:
        raise SnapshotError("failed", f"Deletion error: {e.message}") from e
    except (lvm_storage.LvmError, libvirt.libvirtError) as e:
        raise SnapshotError("failed", f"Deletion error: {describe_exception(e)}") from e
