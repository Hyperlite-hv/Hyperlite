"""Moving a VM disk to another storage pool (Proxmox "Move disk", vSphere "Storage vMotion" for one disk).

- VM running: libvirt `blockCopy` mirrors the disk into a new qcow2 file in the destination pool while the guest
  keeps writing, then the job pivots the VM onto the copy. A persistent domain needs VIR_DOMAIN_BLOCK_COPY_
  TRANSIENT_JOB (libvirt refuses otherwise with "domain is not transient"); the persistent definition is checked
  after the pivot and fixed if this libvirt did not update it.
- VM stopped: `qemu-img convert` to the destination, then the disk's source is changed in the definition.

The copy is always a full qcow2 (a backing chain is flattened, a raw file becomes qcow2). The source file is
deleted only on request and only after the switch succeeded; a failure removes the partial copy and leaves the VM
on its original disk.

Pools: directory and NFS (`dir`, `netfs`) on both sides. ZFS zvols and iSCSI LUNs are refused with a clear
message: they are block devices, not files in a pool directory.
"""

import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core.disk_resize import find_disk
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import FILE_POOL_TYPES, pool_for_path, pool_type_and_target_path

logger = logging.getLogger(__name__)

MIRROR_POLL_S = 1
# A mirror that makes no progress for this long is abandoned rather than left running forever.
MIRROR_STALL_S = 600


class MoveError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def _file_pool(conn, pool_name):
    # Picked from the pools libvirt lists, the requested name only compared: nothing the user typed is handed to
    # libvirt, and the pool XML parsed below always comes from a pool that already exists.
    pool = next((p for p in conn.listAllStoragePools(0) if p.name() == pool_name), None)
    if pool is None:
        raise MoveError(f"Storage pool '{pool_name}' not found", 404)
    kind, target = pool_type_and_target_path(pool)
    if kind not in FILE_POOL_TYPES or not target:
        raise MoveError(
            f"Pool '{pool_name}' is a '{kind or 'unknown'}' pool: disks can only be moved between directory and NFS pools"
        )
    if not pool.isActive():
        raise MoveError(f"Pool '{pool_name}' is not active")
    return pool, target


def plan(conn, domain, target_dev, dest_pool_name):
    """Check everything that can be checked before copying a byte. Returns a dict with the source and destination
    paths, pools and the disk capacity; raises MoveError otherwise."""
    disk_el = find_disk(domain, target_dev)
    if disk_el is None:
        raise MoveError(f"Disk '{target_dev}' not found on VM '{domain.name()}'", 404)
    if disk_el.get("device") != "disk":
        raise MoveError("Only hard disks can be moved, not CD-ROM or floppy drives")
    source_el = disk_el.find("source")
    driver_el = disk_el.find("driver")
    src = source_el.get("file") if disk_el.get("type") == "file" and source_el is not None else None
    if not src:
        raise MoveError("This disk is a block device (ZFS zvol or iSCSI LUN): moving it is not supported yet")
    if driver_el is None or driver_el.get("type") not in ("qcow2", "raw"):
        raise MoveError("Only qcow2 and raw disks can be moved")
    src_pool = pool_for_path(conn, src)
    if src_pool is None:
        raise MoveError("The disk file is in no storage pool: only disks in a directory or NFS pool can be moved")
    src_kind, _ = pool_type_and_target_path(src_pool)
    if src_kind not in FILE_POOL_TYPES:
        raise MoveError(f"The disk is in a '{src_kind}' pool: only directory and NFS pools are supported")
    if src_pool.name() == dest_pool_name:
        raise MoveError(f"The disk is already in pool '{dest_pool_name}'")
    dest_pool, dest_dir = _file_pool(conn, dest_pool_name)

    # Internal qcow2 snapshots live inside the file and are not carried over by a mirror or a convert: moving
    # would silently lose them.
    if domain.snapshotNum(0) > 0:
        raise MoveError("This VM has snapshots, which would be lost by the move: delete or restore them first", 409)
    if domain.isActive():
        try:
            if domain.blockJobInfo(target_dev, 0):
                raise MoveError(f"A block job (backup, migration or copy) is running on '{target_dev}'", 409)
        except libvirt.libvirtError:
            logger.debug("blockJobInfo failed for %s", target_dev, exc_info=True)

    dest = str(Path(dest_dir) / Path(src).name)
    if Path(dest).exists():
        raise MoveError(f"A file '{Path(src).name}' already exists in pool '{dest_pool_name}'", 409)
    capacity = domain.blockInfo(target_dev)[0]
    return {
        "source": src,
        "dest": dest,
        "source_pool": src_pool,
        "dest_pool": dest_pool,
        "capacity": capacity,
        "live": bool(domain.isActive()),
    }


def _disk_config_path(domain, target_dev):
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    for disk_el in root.findall("./devices/disk"):
        target = disk_el.find("target")
        if target is not None and target.get("dev") == target_dev:
            return disk_el
    return None


def _point_config_at(domain, target_dev, dest):
    """Make the persistent definition use `dest` (qcow2) for this disk."""
    disk_el = _disk_config_path(domain, target_dev)
    if disk_el is None:
        raise MoveError(f"Disk '{target_dev}' vanished from the VM definition", 500)
    disk_el.find("source").set("file", dest)
    driver_el = disk_el.find("driver")
    if driver_el is not None:
        driver_el.set("type", "qcow2")
    domain.updateDeviceFlags(ET.tostring(disk_el, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_CONFIG)


CANCELLED = "The move was cancelled; the VM stays on its source disk"


def _stopped(stop):
    return stop is not None and stop.is_set()


def _mirror_live(domain, target_dev, dest, progress, stop=None):
    dest_xml = f"<disk type='file'><driver type='qcow2'/><source file='{dest}'/></disk>"
    flags = libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT | libvirt.VIR_DOMAIN_BLOCK_COPY_TRANSIENT_JOB
    domain.blockCopy(target_dev, dest_xml, {}, flags)
    last_cur, last_move = -1, time.monotonic()
    try:
        while True:
            if _stopped(stop):
                raise MoveError(CANCELLED, 409)
            info = domain.blockJobInfo(target_dev, 0)
            if not info:
                raise MoveError("The copy job ended unexpectedly", 500)
            cur, end = info.get("cur", 0), info.get("end", 0)
            if end:
                progress(min(99, int(cur * 100 / end)))
            if end and cur == end:
                break
            if cur != last_cur:
                last_cur, last_move = cur, time.monotonic()
            elif time.monotonic() - last_move > MIRROR_STALL_S:
                raise MoveError("The copy made no progress for 10 minutes and was cancelled", 500)
            time.sleep(MIRROR_POLL_S)
        domain.blockJobAbort(target_dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
    except Exception:
        # Cancel the mirror (the VM stays on its source disk) before the partial copy is removed.
        try:
            domain.blockJobAbort(target_dev, 0)
        except libvirt.libvirtError:
            logger.debug("No copy job to cancel on %s", target_dev, exc_info=True)
        raise
    # Some libvirt versions pivot the live disk but leave the persistent definition on the old file.
    config_el = _disk_config_path(domain, target_dev)
    if config_el is None or config_el.find("source").get("file") != dest:
        _point_config_at(domain, target_dev, dest)


def _convert_cold(source, dest, progress, stop=None):
    proc = subprocess.Popen(
        ["qemu-img", "convert", "-p", "-O", "qcow2", source, dest],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def watch():
        # qemu-img prints its progress now and then: stop it as soon as asked, not at its next line.
        while proc.poll() is None:
            if stop.wait(0.5):
                proc.terminate()
                return

    if stop is not None:
        threading.Thread(target=watch, daemon=True, name="disk-move-cancel").start()
    try:
        for line in iter(proc.stdout.readline, ""):
            pct = line.strip().split("/")[0].strip("( %)")
            try:
                progress(min(99, int(float(pct))))
            except ValueError:
                continue
    finally:
        stderr = proc.stderr.read()
        proc.wait()
    if _stopped(stop):
        raise MoveError(CANCELLED, 409)
    if proc.returncode != 0:
        raise MoveError(f"qemu-img convert failed: {stderr.strip()[:400]}", 500)


def _refresh(pool):
    try:
        pool.refresh(0)
    except libvirt.libvirtError:
        logger.debug("Pool refresh failed", exc_info=True)


def move(domain, target_dev, how, delete_source, progress=lambda pct: None, stop=None):
    """Copy the disk to the destination pool and switch the VM to it. `how` comes from plan(). `stop`: an Event
    that cancels the copy when set (the partial destination is removed, the VM keeps its source disk)."""
    src, dest = how["source"], how["dest"]
    try:
        if how["live"]:
            subprocess.run(
                ["qemu-img", "create", "-q", "-f", "qcow2", dest, str(how["capacity"])],
                check=True,
                capture_output=True,
                text=True,
            )
            _mirror_live(domain, target_dev, dest, progress, stop)
        else:
            _convert_cold(src, dest, progress, stop)
            _point_config_at(domain, target_dev, dest)
    except libvirt.libvirtError as e:
        Path(dest).unlink(missing_ok=True)
        raise MoveError(f"Move failed: {describe_exception(e)}", 500) from e
    except subprocess.CalledProcessError as e:
        Path(dest).unlink(missing_ok=True)
        raise MoveError(f"Cannot create the destination file: {(e.stderr or '').strip()[:300]}", 500) from e
    except Exception:
        Path(dest).unlink(missing_ok=True)
        raise

    deleted = False
    if delete_source:
        try:
            Path(src).unlink()
            deleted = True
        except OSError:
            logger.warning("Moved %s but could not delete the source %s", target_dev, src, exc_info=True)
    _refresh(how["source_pool"])
    _refresh(how["dest_pool"])
    progress(100)
    return {"source": src, "destination": dest, "source_supprimee": deleted}
