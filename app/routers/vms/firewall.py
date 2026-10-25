import logging

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import privileged, vm_firewall
from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)

# The rules' values, shared with the networks' firewall (app/routers/network.py).
_FIREWALL_PROTOCOLS = vm_firewall.PROTOCOLS
_FIREWALL_ACTIONS = vm_firewall.ACTIONS
_FIREWALL_DIRECTIONS = vm_firewall.DIRECTIONS


class FirewallRule(BaseModel):
    action: str
    direction: str
    protocol: str
    port: int | None = Field(None, ge=1, le=65535)


class FirewallConfig(BaseModel):
    default_policy: str = "accept"
    rules: list[FirewallRule] = []


@router.get("/{name}/firewall")
def get_vm_firewall(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        try:
            nwf = conn.nwfilterLookupByName(vm_firewall.filter_name(name))
            return vm_firewall.parse(nwf.XMLDesc(0))
        except libvirt.libvirtError:
            return {
                "default_policy": "accept",
                "rules": [],
            }  # no rule defined: everything is allowed, the default behaviour
    finally:
        conn.close()


@router.put("/{name}/firewall")
def set_vm_firewall(name: str, payload: FirewallConfig, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if payload.default_policy not in _FIREWALL_ACTIONS:
        raise HTTPException(status_code=422, detail="default_policy must be 'accept' or 'drop'")
    for rule in payload.rules:
        if (
            rule.action not in _FIREWALL_ACTIONS
            or rule.direction not in _FIREWALL_DIRECTIONS
            or rule.protocol not in _FIREWALL_PROTOCOLS
        ):
            raise HTTPException(status_code=422, detail=f"Invalid rule: {rule}")

    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_firewall", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        # Defined and referenced on every interface by hyperlite-daemon (app/core/vm_firewall.py).
        applied = privileged.ask(
            "vm.set_firewall", user, "set_vm_firewall", name, vm=name, config=payload.model_dump()
        )["applied"]
        log_action(
            user["username"],
            "set_vm_firewall",
            name,
            "success",
            f"{len(payload.rules)} rule(s), {applied} interface(s)",
        )
        return {"message": f"Firewall applied to {applied} interface(s)", **payload.model_dump()}
    finally:
        conn.close()


# --- Snapshots ---
#
# A snapshot captures the state of a VM (disk, and memory if it is running) at an
# instant T, stored INSIDE the qcow2 file itself (an "internal" snapshot): it is
# fast to create and restore but it is NOT an independent backup (if the qcow2
# disk is lost or corrupted, so are all its snapshots). A real backup is a
# complete, self-contained copy of the data stored elsewhere, which survives the
# loss of the source disk: slower and heavier, but the only protection against a
# storage failure. A snapshot is for going back quickly (before a risky update,
# for example); a backup is for disaster recovery.
#
# Design notes:
# - The former code (flags=0, minimal XML, synchronous) created and restored
#   internal snapshots correctly. The real reproducible bug was that delete_vm()
#   called domain.undefine() WITHOUT a flag, which simply fails as soon as one or
#   more snapshots still exist ("cannot delete inactive domain with N
#   snapshots"). This is fixed above (VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA):
#   a VM on which a snapshot had been taken became impossible to delete from the
#   interface, which very probably explains the "snapshots do not work" feeling.
# - Deliberately rejected option: a "memoryless" snapshot on a RUNNING VM is
#   really an EXTERNAL snapshot on the libvirt side (a new overlay file, a chain of
#   backing files). It works at creation, but `revertToSnapshot()` returns "revert
#   to external snapshot not supported yet" with this QEMU/libvirt driver, so it
#   can NOT be restored. Offering an "include memory" checkbox that would produce
#   unrecoverable snapshots would have been a new trap, not a fix. The
#   memory/no-memory choice is therefore NOT exposed: memory is included
#   automatically if the VM is running (the only mode that restores reliably), and
#   the disk only if it is stopped (nothing else to capture).
# - Real duration: creating or restoring a snapshot with memory can take several
#   seconds (serializing the whole VM RAM into the qcow2). libvirt exposes NO
#   usable progress statistic for this operation (domain.jobStats() returns
#   {'type': VIR_DOMAIN_JOB_NONE} from start to end), so displaying a percentage
#   would be made up. Create and restore therefore run in the background (a
#   dedicated thread + a separate libvirt connection) while the HTTP endpoint
#   immediately returns a task_id (see app.core.tasks): the frontend shows an
#   indeterminate progress bar and the real elapsed time by following
#   GET /tasks/{id}, instead of blocking the request or showing a fake percentage.
