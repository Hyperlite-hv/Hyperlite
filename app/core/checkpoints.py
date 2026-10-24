"""libvirt checkpoints, the dirty bitmaps behind incremental replication (app/core/replication.py).

Verified on libvirt 10 and QEMU 8.2 (docs/design/replication.md section 5.7):
  - while a checkpoint exists, libvirt refuses "block operations": the transient external snapshot of a hot backup,
    an offline snapshot, a disk move, a migration that copies the disk, a disk resize;
  - a checkpoint cannot be deleted while its VM is stopped;
  - deleting only the metadata leaves the bitmap inside the qcow2 file.

So an operation libvirt would refuse first calls `release(domain)`, which drops every checkpoint the right way for the
VM's state (and the bitmaps with qemu-img when it is stopped). The replication chain is then broken, and its next
copy is a full one.
"""

import logging
import subprocess
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)

PREFIX = "hl-"


def names(domain):
    try:
        return [c.getName() for c in domain.listAllCheckpoints()]
    except libvirt.libvirtError:
        logger.debug("Cannot list the checkpoints of %s", domain.name(), exc_info=True)
        return []


def _qcow2_files(domain):
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    files = []
    for disk in root.findall("./devices/disk"):
        driver, source = disk.find("driver"), disk.find("source")
        is_file = disk.get("type") == "file" and disk.get("device") == "disk"
        if (
            is_file
            and driver is not None
            and driver.get("type") == "qcow2"
            and source is not None
            and source.get("file")
        ):
            files.append(source.get("file"))
    return files


def _remove_bitmap(path, name):
    proc = subprocess.run(["qemu-img", "bitmap", "--remove", path, name], capture_output=True, text=True, timeout=300)
    # A bitmap already gone is the state we want.
    if proc.returncode != 0 and "not found" not in (proc.stderr or "").lower():
        raise RuntimeError(f"Cannot remove the replication bitmap {name} of {path}: {proc.stderr.strip()[:200]}")


def release(domain, vm_name=None):
    """Drop every checkpoint of `domain`, so libvirt allows block operations again, and record that the replication
    chain must start again. Returns the number of checkpoints dropped."""
    dropped = drop(domain)
    if dropped:
        forget(vm_name or domain.name(), dropped)
    return dropped


def forget(vm_name, dropped):
    """The replication chain of `vm_name` starts again: its checkpoints were dropped (by drop(), here or by
    hyperlite-daemon, which leaves the database to the API)."""
    from app.repositories import registry

    registry.replication().sync.forget_chain(vm_name)
    logger.info("Dropped %d replication checkpoint(s) of %s", dropped, vm_name)


def drop(domain):
    """The root part of release(): every checkpoint of `domain` deleted, with its bitmaps in a stopped VM's qcow2
    files. Returns the number dropped."""
    found = domain.listAllCheckpoints() if names(domain) else []
    if not found:
        return 0
    if domain.isActive():
        # Children first: libvirt refuses to delete a checkpoint that others depend on.
        for checkpoint in reversed(found):
            checkpoint.delete(0)
    else:
        files = _qcow2_files(domain)
        for checkpoint in reversed(found):
            name = checkpoint.getName()
            checkpoint.delete(libvirt.VIR_DOMAIN_CHECKPOINT_DELETE_METADATA_ONLY)
            for path in files:
                _remove_bitmap(path, name)
    return len(found)
