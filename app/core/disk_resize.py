"""Growing a VM disk.

How depends on where the disk lives:
  - qcow2 or raw file (dir and NFS pools): `blockResize` when the VM runs, so QEMU grows the image and the guest sees
    the new size at once; `qemu-img resize` when it is stopped.
  - ZFS zvol: `zfs set volsize`, then `blockResize` when the VM runs, so QEMU (and the guest) picks up the new size.
  - iSCSI LUN: refused. The LUN is sized on the storage server; Hyperlite cannot make it bigger.

Only growing is allowed. A smaller size cuts the end of the guest's disk, which destroys the data there unless the
guest shrank its file system first, and nothing here can check that.

The guest still has to extend its partition and file system (cloud images do it at boot with growpart); this module
only changes the size of the virtual disk.
"""

import logging
import subprocess
import xml.etree.ElementTree as ET

import libvirt

from app.core import iscsi, zfs_storage
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import refresh_pools_for_paths

logger = logging.getLogger(__name__)

GIB = 1024**3
QEMU_IMG_TIMEOUT_S = 120
FILE_FORMATS = ("qcow2", "raw")

# Why a disk cannot be grown, as a stable code the dashboard translates.
NOT_GROWABLE_ISCSI = "iscsi"
NOT_GROWABLE_UNSUPPORTED = "non_gere"


class ResizeError(Exception):
    """A resize refused or failed; `status` is the HTTP status the endpoint answers with."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def find_disk(domain, target_dev):
    """The <disk> element whose target is `target_dev`, or None."""
    root = ET.fromstring(domain.XMLDesc(0))
    for disk_el in root.findall("./devices/disk"):
        target = disk_el.find("target")
        if target is not None and target.get("dev") == target_dev:
            return disk_el
    return None


def plan(disk_el):
    """How this disk can be grown: ({"kind": "file", "path", "format"} or {"kind": "zvol", "pool", "zvol"}, None),
    or (None, (code, message)) when it cannot."""
    if disk_el.get("device") != "disk":
        return None, (NOT_GROWABLE_UNSUPPORTED, "Only hard disks can be resized, not CD-ROM or floppy drives")
    source = disk_el.find("source")
    driver = disk_el.find("driver")
    kind = disk_el.get("type")
    if kind == "file":
        path = source.get("file") if source is not None else None
        fmt = driver.get("type") if driver is not None else None
        if not path:
            return None, (NOT_GROWABLE_UNSUPPORTED, "This disk has no source file")
        if fmt not in FILE_FORMATS:
            return None, (
                NOT_GROWABLE_UNSUPPORTED,
                f"Disks in the '{fmt or 'unknown'}' format cannot be resized here (qcow2 and raw only)",
            )
        return {"kind": "file", "path": path, "format": fmt}, None
    if kind == "block":
        dev = source.get("dev") if source is not None else None
        if iscsi.is_iscsi_device(dev):
            return None, (
                NOT_GROWABLE_ISCSI,
                "This disk is an iSCSI LUN: grow the LUN on the storage server, Hyperlite cannot resize it",
            )
        parts = (dev or "").strip("/").split("/")
        # /dev/zvol/<pool>/<zvol>: the only layout create_zvol makes. Nested datasets are not ours.
        if len(parts) == 4 and parts[:2] == ["dev", "zvol"]:
            pool, zvol = parts[2], parts[3]
            if zfs_storage.validate_zfs_name(pool) is None and zfs_storage.validate_zfs_name(zvol) is None:
                return {"kind": "zvol", "pool": pool, "zvol": zvol}, None
        return None, (NOT_GROWABLE_UNSUPPORTED, "This block device is not managed by Hyperlite and cannot be resized")
    return None, (NOT_GROWABLE_UNSUPPORTED, f"Disks of type '{kind or 'unknown'}' cannot be resized here")


def not_growable_code(disk_el):
    """None when the disk can be grown, else the code of the reason (for the disk list)."""
    _plan, refusal = plan(disk_el)
    return refusal[0] if refusal else None


def _qemu_img_resize(path, fmt, size_bytes):
    try:
        subprocess.run(
            ["qemu-img", "resize", "-f", fmt, path, str(size_bytes)],
            check=True,
            capture_output=True,
            text=True,
            timeout=QEMU_IMG_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as e:
        raise ResizeError(f"qemu-img resize failed: {(e.stderr or e.stdout or '').strip()[:400]}", 500) from e
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ResizeError(f"qemu-img resize could not run: {e}", 500) from e


def _refresh_pool_of(conn, path):
    # A stopped VM's file is grown behind libvirt's back: refresh its pool so the Storage page shows the new size.
    refresh_pools_for_paths(conn, [path])


def grow(conn, domain, target_dev, size_gb):
    """Grow disk `target_dev` of `domain` to `size_gb` GiB. Returns (old_bytes, new_bytes, live). Raises
    ResizeError when the disk is missing, cannot be grown, the size is not larger, or the operation fails."""
    disk_el = find_disk(domain, target_dev)
    if disk_el is None:
        raise ResizeError(f"Disk '{target_dev}' not found on VM '{domain.name()}'", 404)
    how, refusal = plan(disk_el)
    if refusal:
        raise ResizeError(refusal[1])

    try:
        old_bytes = domain.blockInfo(target_dev)[0]
    except libvirt.libvirtError as e:
        raise ResizeError(f"Cannot read the current size of '{target_dev}': {describe_exception(e)}", 500) from e
    new_bytes = int(size_gb) * GIB
    if new_bytes < old_bytes:
        raise ResizeError(
            f"Shrinking a disk is not supported ({old_bytes / GIB:.2f} GB now, {size_gb} GB requested): "
            "it would cut the end of the guest's disk"
        )
    if new_bytes == old_bytes:
        raise ResizeError(f"Disk '{target_dev}' is already {size_gb} GB")

    live = bool(domain.isActive())
    if live:
        # A backup, migration or copy job owns the disk's image chain until it ends; resizing under it would
        # leave the copy and the source disagreeing on the size.
        try:
            job = domain.blockJobInfo(target_dev, 0)
        except libvirt.libvirtError:
            logger.debug("blockJobInfo failed for %s", target_dev, exc_info=True)
            job = None
        if job:
            raise ResizeError(
                f"A block job (backup, migration or copy) is running on '{target_dev}': retry when it ends", 409
            )

    if how["kind"] == "zvol":
        try:
            zfs_storage.grow_zvol(how["pool"], how["zvol"], new_bytes)
        except zfs_storage.ZfsError as e:
            raise ResizeError(f"zvol resize error: {e.message}", 500) from e
    elif not live:
        _qemu_img_resize(how["path"], how["format"], new_bytes)
        _refresh_pool_of(conn, how["path"])

    if live:
        # For a file this grows the image; for a zvol, already grown above, it makes QEMU re-read the size and
        # tell the guest.
        try:
            domain.blockResize(target_dev, new_bytes, libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES)
        except libvirt.libvirtError as e:
            raise ResizeError(f"Live resize error: {describe_exception(e)}", 500) from e
    return old_bytes, new_bytes, live
