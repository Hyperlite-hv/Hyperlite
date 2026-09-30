"""Renaming a VM: libvirt renames the domain (its disks keep their file names, as elsewhere), and what Hyperlite keeps
under the VM's name follows it (app/core/renaming.py).

libvirt renames only a stopped VM without snapshots. The drives Hyperlite generated for the VM itself (cloud-init,
unattended installation) are recognized by their file name, so they are renamed with it: otherwise deleting the VM
would leave them behind, and editing its cloud-init would not find its drive. So is its UEFI variable store.
"""

import logging
import os
import shlex
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import renaming, vm_locks
from app.core.audit import log_action
from app.core.cluster import _run_ssh, get_node
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.security import require_role
from app.core.vm_builder import validate_name
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)

GENERATED_ISO_SUFFIXES = ("-cloudinit.iso", "-oemdrv.iso", "-autoinstall.iso")
NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"


class RenameRequest(BaseModel):
    new_name: str


def _own_files(root, old, new):
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


class _Files:
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


def _rename_own_files(conn, domain, old, new, node):
    """Rename the files named after the VM and point its definition at them; undone if the definition is refused."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    files = _Files(node)
    moved, changed = [], False
    for element, attribute, path, target in _own_files(root, old, new):
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


@router.post("/{name}/rename")
def rename_vm(name: str, payload: RenameRequest, node: str | None = None, user: dict = Depends(require_role("admin"))):
    new = payload.new_name
    error = validate_name(new, "VM")
    if error:
        raise HTTPException(status_code=422, detail=error)
    if new == name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    cluster = renaming.vm_in_k8s_cluster(name)
    if cluster:
        raise HTTPException(
            status_code=409,
            detail=f"VM '{name}' belongs to the Kubernetes cluster '{cluster}', which finds its VMs by name",
        )
    with vm_locks.claim_or_409(name, "a rename", node):
        conn = open_conn(node)
        try:
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
            try:
                conn.lookupByName(new)
                raise HTTPException(status_code=409, detail=f"A VM named '{new}' already exists")
            except libvirt.libvirtError:
                logger.debug("No VM named %s yet", new)
            if domain.isActive():
                raise HTTPException(status_code=409, detail="Stop the VM before renaming it")
            if domain.snapshotNum(0) > 0:
                raise HTTPException(
                    status_code=409, detail="libvirt cannot rename a VM that has snapshots: delete them first"
                )
            try:
                domain.rename(new, 0)
                domain = conn.lookupByName(new)
                _rename_own_files(conn, domain, name, new, node)
            except (libvirt.libvirtError, OSError) as e:
                msg = describe_exception(e)
                log_action(user["username"], "rename_vm", name, "echec", msg)
                raise HTTPException(status_code=500, detail=f"Rename failed: {msg}") from e
            renaming.vm_records(name, new, node)
            log_action(user["username"], "rename_vm", name, "succes", f"-> {new}")
            return _domain_summary(domain)
        finally:
            conn.close()
