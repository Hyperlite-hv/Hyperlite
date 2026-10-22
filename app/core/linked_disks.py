"""Linked clones: a VM's disk that only stores its own changes over a template's disk, as Proxmox VE's linked clones.

A linked disk is a qcow2 image whose backing file is the template's disk: it is created in an instant and stays
small, and QEMU reads through it to the template for every block the VM has not written. The template's disk is then
shared by every clone made from it, read-only, and must stay where it is, unchanged, for as long as one of them
exists: `dependents()` names them, so that removing, renaming or replacing the template can be refused.

The backing file is read from the qcow2 header itself (the format's specification: magic, version, then the
backing file's offset and length), not with `qemu-img info`: no process per disk when every VM's disks are checked,
and the header of a running VM's disk can be read while QEMU holds it.
"""

import logging
import os
import struct
import subprocess
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)

QCOW2_MAGIC = b"QFI\xfb"
# A chain deeper than this is not one Hyperlite made (a template's disk has no backing file of its own): stop there.
MAX_DEPTH = 16


class LinkedDiskError(Exception):
    pass


def backing_file(path):
    """The absolute path of a qcow2 image's backing file, or None (no backing file, not a qcow2 image, unreadable).
    A relative backing file name is relative to the image's own directory, as QEMU reads it."""
    try:
        with open(path, "rb") as f:
            header = f.read(20)
            if len(header) < 20 or header[:4] != QCOW2_MAGIC:
                return None
            offset, size = struct.unpack(">QI", header[8:20])
            if not offset or not size or size > 1023:
                return None
            f.seek(offset)
            name = f.read(size).decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("No backing file read from %s: %s", path, e)
        return None
    if name.startswith(("json:", "nbd:", "rbd:", "http:", "https:")):
        return name  # a protocol, not a file: kept as QEMU wrote it
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), name))


def chain(path):
    """[path, its backing file, that one's backing file, ...]: the images a disk reads through."""
    out = [os.path.abspath(path)]
    while len(out) < MAX_DEPTH:
        nxt = backing_file(out[-1])
        if not nxt or nxt in out:
            break
        out.append(nxt)
    return out


def depends_on(path, base):
    """True when the disk at `path` reads through `base` (directly, or through another linked disk)."""
    target = os.path.realpath(base)
    return any(os.path.realpath(p) == target for p in chain(path)[1:])


def create_linked(base, dest):
    """Create `dest`, a qcow2 image backed by `base` (a qcow2 image), of the same virtual size. Raises LinkedDiskError."""
    if not is_qcow2(base):
        raise LinkedDiskError(f"{base} is not a qcow2 image: a linked clone needs one")
    if os.path.exists(dest):
        raise LinkedDiskError(f"{dest} already exists")
    try:
        subprocess.run(
            ["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", os.path.abspath(base), str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise LinkedDiskError((e.stderr or e.stdout or str(e)).strip()) from e


def is_qcow2(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == QCOW2_MAGIC
    except OSError:
        return False


def domain_disks(conn):
    """(VM name, disk file) for every disk file of every VM defined on `conn`, running or not."""
    out = []
    for dom in conn.listAllDomains():
        try:
            root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except libvirt.libvirtError as e:
            logger.debug("Domain vanished while its disks were read: %s", e)
            continue
        for disk in root.findall("devices/disk[@device='disk']"):
            source = disk.find("source")
            path = source.get("file") if source is not None else None
            if path:
                out.append((dom.name(), path))
    return out


def dependents(base, conn):
    """Names of the VMs on `conn` with a disk that reads through `base`, sorted."""
    return sorted({name for name, path in domain_disks(conn) if depends_on(path, base)})
