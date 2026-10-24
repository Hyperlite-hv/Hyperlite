"""Renaming a VM on its node (app/routers/vms/rename.py): libvirt renames the domain (its disks keep their file
names, as elsewhere); the drives Hyperlite generated for it and its UEFI variable store follow its name. This node's
VM is renamed by hyperlite-daemon (app/daemon/ops/vm.py, docs/design/privilege-separation.md, section 13); another
node's over its own libvirt and SSH, until the cluster domain.

libvirt renames only a stopped VM without snapshots."""

import os
import shlex
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import libvirt

from app.core.cluster import _run_ssh, get_node
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.vm_builder import GENERATED_ISO_SUFFIXES

# -oemdrv and -autoinstall: the Kickstart/autoinstall answers of VMs created before 1.0.
NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"


class RenameError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def own_files(root, old, new):
    """(element, attribute, current path, new path) for the files named after the VM: the drives Hyperlite generated
    for it, and the UEFI variables libvirt keeps at /var/lib/libvirt/qemu/nvram/<name>_VARS.fd (a later VM taking the old name would
    otherwise be given the same NVRAM file)."""
    items = []
    for source in root.findall("./devices/disk[@device='cdrom']/source[@file]"):
        path = PurePosixPath(source.get("file"))
        for suffix in GENERATED_ISO_SUFFIXES:
            if path.name == f"{old}{suffix}":
                items.append((source, "file", path, PurePosixPath(safe_child(path.parent, f"{new}{suffix}"))))
    nvram = root.find("./os/nvram")
    if nvram is not None and (nvram.text or "").strip():
        path = PurePosixPath(nvram.text.strip())
        # Named after the VM by libvirt, or after an older name when the VM was renamed outside Hyperlite.
        if str(path.parent) == NVRAM_DIR and path.name.endswith("_VARS.fd") and path.name != f"{new}_VARS.fd":
            items.append((nvram, None, path, PurePosixPath(safe_child(path.parent, f"{new}_VARS.fd"))))
    return items


class Files:
    """Existence checks and renames on the VM's host: this one, or a registered node over SSH."""

    def __init__(self, node):
        self.node = get_node(node) if node else None

    def exists(self, path):
        if self.node is None:
            return os.path.lexists(path)
        # ssh hands its arguments to the node's shell as one line: each path is quoted for it.
        return _run_ssh(self.node, ["test", "-e", shlex.quote(str(path))]).returncode == 0

    def rename(self, path, target):
        if self.node is None:
            os.rename(path, target)
            return
        result = _run_ssh(self.node, ["mv", "-n", "--", shlex.quote(str(path)), shlex.quote(str(target))])
        if result.returncode != 0:
            raise OSError(f"mv {path} {target} on node '{self.node['name']}': {result.stderr.strip()}")


def rename_own_files(conn, domain, old, new, node):
    """Rename the files named after the VM and point its definition at them; undone if the definition is refused."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    files = Files(node)
    moved, changed = [], False
    for element, attribute, path, target in own_files(root, old, new):
        if files.exists(target):
            continue  # another file already has that name: keep this one where it is
        if files.exists(path):
            files.rename(path, target)
            moved.append((path, target))
        # A file not created yet (NVRAM before the first start) only changes in the definition.
        if attribute:
            element.set(attribute, str(target))
        else:
            element.text = str(target)
        changed = True
    if not changed:
        return
    try:
        conn.defineXML(ET.tostring(root, encoding="unicode"))
    except libvirt.libvirtError:
        for path, target in moved:
            files.rename(target, path)
        raise
    if node is None:
        refresh_pools_for_paths(conn, [t for _p, t in moved])


def rename(conn, domain, old, new, node=None):
    """The VM renamed, then its own files; refused while it runs, has snapshots, or the name is taken. Returns the
    number of replication checkpoints dropped (the chain follows the name of the VM's directory on the other site:
    the caller forgets it)."""
    from app.core import checkpoints

    try:
        conn.lookupByName(new)
        raise RenameError("in_use", f"A VM named '{new}' already exists")
    except libvirt.libvirtError:
        pass
    if domain.isActive():
        raise RenameError("in_use", "Stop the VM before renaming it")
    if domain.snapshotNum(0) > 0:
        raise RenameError("in_use", "libvirt cannot rename a VM that has snapshots: delete them first")
    try:
        dropped = checkpoints.drop(domain)
        domain.rename(new, 0)
        rename_own_files(conn, conn.lookupByName(new), old, new, node)
    except (libvirt.libvirtError, OSError) as e:
        raise RenameError("failed", f"Rename failed: {describe_exception(e)}") from e
    return dropped
