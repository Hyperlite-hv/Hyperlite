"""Renaming a VM: libvirt renames the domain (its disks keep their file names, as elsewhere), and what Hyperlite keeps
under the VM's name follows it (app/core/renaming.py).

libvirt renames only a stopped VM without snapshots. The drives Hyperlite generated for the VM itself (cloud-init,
unattended installation) are recognized by their file name, so they are renamed with it: otherwise deleting the VM
would leave them behind, and editing its cloud-init would not find its drive. So is its UEFI variable store.
"""

import logging

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import checkpoints, privileged, renaming, replication, vm_locks
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.security import require_role
from app.core.vm_builder import validate_name
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


class RenameRequest(BaseModel):
    new_name: str


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
        conn = open_conn(node, readonly=True)
        try:
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
            # Renamed with its own files by hyperlite-daemon (app/core/vm_rename.py), here or on the VM's node.
            remote = node if node and node != "local" else None
            dropped = privileged.ask("vm.rename", user, "rename_vm", name, vm=name, new_name=new, node=remote)[
                "dropped"
            ]
            # The replication chain follows the name of the VM's directory on the other site: start a new one.
            if dropped:
                checkpoints.forget(name, dropped)
            replication.forget(name)
            domain = conn.lookupByName(new)
            renaming.vm_records(name, new, node)
            log_action(user["username"], "rename_vm", name, "success", f"-> {new}")
            return _domain_summary(domain)
        finally:
            conn.close()
