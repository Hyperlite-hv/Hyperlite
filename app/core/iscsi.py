"""iSCSI storage: a target on a NAS or storage array whose LUNs (disks created on the storage side) become the
disks of VMs.

The pool is a libvirt 'iscsi' pool (storage backend libvirt_storage_backend_iscsi, host initiator open-iscsi): libvirt
logs in to the target and lists its LUNs as volumes, each one a block device under /dev/disk/by-path. Unlike a
dir/netfs/ZFS pool, Hyperlite cannot create or resize a disk there: LUNs are made on the storage side, and a VM takes
a free one whole. Checked on a real LIO target before this module was written.

A VM on iSCSI LUNs has raw block disks that are not ZFS zvols: backups, snapshots, clones, exports, migration and
disk resizing do not handle them yet (they would copy nothing or fail halfway), so they refuse such a VM with a clear
message (refuse_if_iscsi) instead of reporting a success.
"""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import HTTPException

INITIATOR_FILE = Path("/etc/iscsi/initiatorname.iscsi")
BY_PATH = "/dev/disk/by-path"
DEFAULT_PORT = 3260

# iqn.YYYY-MM.reversed.domain[:anything], or the eui. form. Nothing that could leave the XML attribute or a shell word.
IQN_RE = re.compile(r"^(iqn\.\d{4}-\d{2}\.[a-z0-9][a-z0-9.-]*(:[A-Za-z0-9._:-]{1,200})?|eui\.[0-9A-Fa-f]{16})$")
# CHAP user name: letters, digits and a few separators (it ends up in the pool XML).
CHAP_USER_RE = re.compile(r"^[A-Za-z0-9._@:-]{1,64}$")


def initiator_available():
    """The host initiator (open-iscsi) that libvirt's iscsi backend drives."""
    return shutil.which("iscsiadm") is not None


def initiator_name():
    """This host's iSCSI initiator name: the storage side must allow it (ACL) before any LUN is visible."""
    try:
        for line in INITIATOR_FILE.read_text().splitlines():
            if line.startswith("InitiatorName="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def secret_usage(pool_name):
    """Usage id of the libvirt secret that holds a pool's CHAP password."""
    return f"hyperlite-iscsi-{pool_name}"


def secret_xml(pool_name):
    root = ET.Element("secret", ephemeral="no", private="yes")
    ET.SubElement(root, "description").text = f"Hyperlite: CHAP password of the iSCSI pool {pool_name}"
    usage = ET.SubElement(root, "usage", type="iscsi")
    ET.SubElement(usage, "target").text = secret_usage(pool_name)
    return ET.tostring(root, encoding="unicode")


def is_iscsi_device(path):
    return bool(path) and path.startswith(BY_PATH + "/") and "-iscsi-" in path


def iscsi_disks_of_domain(domain):
    """Block device paths of a domain's disks that are iSCSI LUNs."""
    root = ET.fromstring(domain.XMLDesc())
    paths = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("type") != "block" or disk_el.get("device") != "disk":
            continue
        source_el = disk_el.find("source")
        dev = source_el.get("dev") if source_el is not None else None
        if is_iscsi_device(dev):
            paths.append(dev)
    return paths


def refuse_if_iscsi(domain, action):
    """422 for an action not adapted yet to iSCSI disks (see the module docstring)."""
    if iscsi_disks_of_domain(domain):
        raise HTTPException(
            status_code=422,
            detail=f"{action} is not available yet for a VM whose disks are iSCSI LUNs",
        )
