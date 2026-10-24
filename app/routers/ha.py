"""High-availability endpoints. Logic lives in app/core/ha.py (protection, manual recovery), ha_fencing.py (per-node
fencing settings, power off through a BMC), ha_recovery.py (in a cluster, the leader restarts the HA VMs of a lost
member once it is fenced) and ha_watch.py (the dry run of hosts driven over SSH). See docs/design/ha-automatic.md."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import ha, ha_admission, ha_fencing, ha_watch, maintenance
from app.core.audit import log_action
from app.core.listing import item, listing
from app.core.security import get_current_user, require_role
from app.services import node_service

router = APIRouter(prefix="/ha", tags=["ha"])


@router.get("")
async def list_protected(user: dict = Depends(get_current_user)):
    return listing(await _protected())


async def _protected():
    rows = await run_in_threadpool(lambda: [_with_policy(r) for r in ha.list_protected()])
    return [{**row, "node_status": await node_service.status(row["node"])} for row in rows]


class EnableRequest(BaseModel):
    node: str | None = None
    # The order automatic HA restarts the VMs of a lost member in, and where it tries first (4.3).
    priority: Literal["high", "medium", "low"] = "medium"
    preferred_nodes: list[str] = Field(default_factory=list, max_length=16)
    # VM monitoring (4.9): reset in place when its guest agent stops answering and it does no I/O. Off by default.
    monitoring: bool = False


class MonitoringRequest(BaseModel):
    enabled: bool


def _with_policy(row):
    if not row:
        return row
    policy = {k: v for k, v in ha.policy(row["vm_name"]).items() if k != "auto_restarts"}
    return {**row, **policy, "monitoring": ha.monitoring(row["vm_name"])["enabled"]}


@router.post("/{vm_name}/enable", status_code=201)
def enable(vm_name: str, payload: EnableRequest, user: dict = Depends(require_role("admin"))):
    try:
        admission = ha.enable_protection(vm_name, payload.node, user["username"])
        ha.set_policy(vm_name, payload.priority, payload.preferred_nodes)
        ha.set_monitoring(vm_name, payload.monitoring)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    out = _with_policy(ha.get_protected(vm_name))
    if admission and admission["detail"]:
        out["admission"] = admission  # under "warn": accepted, and said
    return out


@router.delete("/{vm_name}")
def disable(vm_name: str, user: dict = Depends(require_role("admin"))):
    if not ha.get_protected(vm_name):
        raise HTTPException(status_code=404, detail=f"'{vm_name}' is not HA-protected")
    ha.disable_protection(vm_name, user["username"])
    return {"message": f"HA protection disabled for '{vm_name}'"}


@router.put("/{vm_name}/monitoring")
def set_monitoring(vm_name: str, payload: MonitoringRequest, user: dict = Depends(require_role("admin"))):
    """VM monitoring of an HA VM on or off (docs/design/ha-automatic.md, 4.9): it needs qemu-guest-agent in the guest;
    a VM whose agent never answered is never reset."""
    if not ha.get_protected(vm_name):
        raise HTTPException(status_code=404, detail=f"'{vm_name}' is not HA-protected")
    ha.set_monitoring(vm_name, payload.enabled)
    log_action(user["username"], "ha_monitoring", vm_name, "success", "on" if payload.enabled else "off")
    return _with_policy(ha.get_protected(vm_name))


class RecoverRequest(BaseModel):
    target_node: str


@router.post("/{vm_name}/recover", status_code=202)
def recover(vm_name: str, payload: RecoverRequest, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance(payload.target_node, "HA recovery")
    try:
        ha.recover(vm_name, payload.target_node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"message": f"'{vm_name}' recovered on '{payload.target_node}'"}


# --- Dry-run watcher, fencing settings, leases.


@router.get("/status")
def watch_status(user: dict = Depends(get_current_user)):
    return {**ha_watch.status(), "admission": ha_admission.check()}


class AdmissionPolicy(BaseModel):
    policy: Literal["warn", "strict", "off"]


@router.put("/admission")
def put_admission(payload: AdmissionPolicy, user: dict = Depends(require_role("admin"))):
    ha_admission.set_policy(payload.policy)
    log_action(user["username"], "ha_admission", "ha", "success", f"policy={payload.policy}")
    return ha_admission.check()


class HaSettings(BaseModel):
    witness: str = Field("", max_length=260)
    suspect_threshold: int = 3
    failure_threshold: int = 6


@router.put("/settings")
def put_settings(payload: HaSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = ha_watch.save_settings(payload.witness, payload.suspect_threshold, payload.failure_threshold)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "ha_settings", "ha", "success", f"witness={payload.witness or 'none'}")
    return result


def _known_node(node):
    if node != "local":
        from app.core.cluster import get_node

        if get_node(node) is None:
            raise HTTPException(status_code=404, detail=f"Node '{node}' not found")


@router.get("/fencing")
def list_fencing(user: dict = Depends(require_role("admin"))):
    return listing(ha_fencing.list_all())


class FencingSettings(BaseModel):
    method: str
    address: str | None = Field(None, max_length=253)
    port: int | None = None
    user: str | None = Field(None, max_length=64)
    # Absent or empty: keep the stored password.
    secret: str | None = Field(None, max_length=256)
    tls_unverified: bool = False


@router.put("/fencing/{node}")
def put_fencing(node: str, payload: FencingSettings, user: dict = Depends(require_role("admin"))):
    _known_node(node)
    try:
        result = ha_fencing.save(
            node,
            payload.method,
            payload.address,
            payload.port,
            payload.user,
            payload.secret or None,
            payload.tls_unverified,
            user["username"],
        )
    except ha_fencing.FencingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e
    log_action(user["username"], "ha_fencing_set", node, "success", payload.method)
    return result


@router.delete("/fencing/{node}")
def delete_fencing(node: str, user: dict = Depends(require_role("admin"))):
    if not ha_fencing.delete(node):
        raise HTTPException(status_code=404, detail="No fencing is set for this node")
    log_action(user["username"], "ha_fencing_delete", node, "success")
    return {"message": "Fencing settings removed"}


@router.post("/fencing/{node}/test")
def test_fencing(node: str, user: dict = Depends(require_role("admin"))):
    """Reads the power state through the BMC or AMT. Never powers anything off."""
    try:
        result = ha_fencing.test(node)
    except ha_fencing.FencingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e
    log_action(
        user["username"], "ha_fencing_test", node, "success" if result["ok"] else "failed", result["detail"][:300]
    )
    return result


@router.get("/leases/{node}")
def lease_check(node: str, user: dict = Depends(require_role("admin"))):
    _known_node(node)
    return ha_watch.lease_check(node)


# Registered last so that the fixed paths above (/status, /fencing...) keep their routes.
@router.get("/{vm_name}")
async def get_protected(vm_name: str, user: dict = Depends(get_current_user)):
    """One protected VM, as GET /ha lists it."""
    return item(await _protected(), "vm_name", vm_name, f"HA protection of '{vm_name}'")
