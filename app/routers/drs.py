"""Load balancing of the cluster (docs/design/drs.md, #334): its settings, its rules, each VM's exception, its state
and the recommended moves, an administrator's "apply", and the member a new VM goes to on "auto"."""

import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import drs, drs_rules
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/drs", tags=["drs"])


class Settings(BaseModel):
    mode: str
    threshold: int = Field(ge=1, le=5)


class Rule(BaseModel):
    name: str
    kind: str
    strength: str = "should"
    vms: list[str] = Field(default_factory=list, max_length=drs_rules.MAX_MEMBERS)
    nodes: list[str] = Field(default_factory=list, max_length=drs_rules.MAX_MEMBERS)
    enabled: bool = True


class Behaviour(BaseModel):
    behaviour: str


class Move(BaseModel):
    vm: str
    source: str
    target: str
    reason: str = "applied by an administrator"


def _state():
    """The leader's state; on another member, what would move now, read from the same statuses."""
    from app import ha_services
    from app.core import statd

    if ha_services.crm_leads():  # this node's HA decisions: its CRM's lead (app/ha_services)
        return {**drs.state(), "leading": True}
    nodes = drs.view(statd.table())
    moves = drs.recommend(nodes, drs_rules.settings()["threshold"], drs_rules.behaviours())
    view = {
        n: {"cpu": round(v["cpu"], 1), "mem": round(v["mem"], 1), "score": round(v["score"], 1)}
        for n, v in nodes.items()
    }
    return {"leading": False, "moves": moves, "nodes": view, "spread": round(drs._spread(nodes), 1), "running": []}


@router.get("")
def get_drs(user: dict = Depends(get_current_user)):
    return {
        "settings": drs_rules.settings(),
        "state": _state(),
        "rules": drs_rules.rules(),
        "behaviours": drs_rules.behaviours(),
    }


@router.put("/settings")
def put_settings(payload: Settings, user: dict = Depends(require_role("admin"))):
    try:
        result = drs_rules.save_settings(payload.mode, payload.threshold)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    drs.resume()  # saved again: automatic mode resumes after a pause
    log_action(user["username"], "drs_settings", "drs", "success", f"mode={payload.mode} threshold={payload.threshold}")
    return result


@router.get("/rules")
def list_rules(user: dict = Depends(get_current_user)):
    return listing(drs_rules.rules())


@router.put("/rules/{name}")
def put_rule(name: str, payload: Rule, user: dict = Depends(require_role("admin"))):
    if payload.name != name:
        raise HTTPException(status_code=422, detail="The rule's name is the one in the path")
    try:
        rule = drs_rules.save_rule(payload.model_dump(), user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "drs_rule", name, "success", f"{rule['kind']} {rule['strength']}")
    return rule


@router.delete("/rules/{name}")
def delete_rule(name: str, user: dict = Depends(require_role("admin"))):
    if not drs_rules.delete_rule(name):
        raise HTTPException(status_code=404, detail=f"Rule '{name}' not found")
    log_action(user["username"], "drs_rule_delete", name, "success")
    return {"message": f"Rule '{name}' deleted"}


@router.put("/vms/{vm_name}")
def put_behaviour(vm_name: str, payload: Behaviour, user: dict = Depends(require_role("admin"))):
    """A VM left to the cluster's mode ("default"), kept in manual while the cluster is automatic ("manual"), or never
    moved by load balancing ("excluded")."""
    try:
        drs_rules.set_behaviour(vm_name, payload.behaviour)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "drs_vm", vm_name, "success", payload.behaviour)
    return {"vm_name": vm_name, "behaviour": payload.behaviour}


@router.post("/apply", status_code=202)
def apply_move(payload: Move, user: dict = Depends(require_role("admin"))):
    """A recommended move carried out by an administrator, through the live migration (its checks, its lock)."""
    return drs.apply(payload.model_dump(), user["username"])


def _missing_on(node, network, storage_pool, username):
    """Why `node` cannot hold a new VM on `network` and `storage_pool` (a sentence), or None. This node is never asked:
    the creation form lists its own."""
    from app.core import cluster_view, self_node

    if self_node.is_self(node):
        return None
    try:
        member = cluster_view.peer(node)
    except LookupError:
        return "it is not a member of this cluster"
    for path, what in (
        (f"/networks/{urllib.parse.quote(network, safe='')}", f"no network '{network}'"),
        (f"/storage/{urllib.parse.quote(storage_pool, safe='')}", f"no storage pool '{storage_pool}'"),
    ):
        try:
            status, _ = cluster_view.forward(member, username, "GET", path)
        except cluster_view.PeerError:
            return "it does not answer"
        if status != 200:
            return what
    return None


@router.get("/placement")
def get_placement(
    memory_mb: int = Query(ge=1),
    vcpus: int = Query(1, ge=1),
    name: str = "",
    network: str = "default",
    storage_pool: str = "default",
    user: dict = Depends(get_current_user),
):
    """The member a new VM goes to when its node is "auto" (docs/design/drs.md, 3.6): the one placement prefers among
    those that have its network and its storage pool. 409 when none can take it."""
    from app.core import self_node

    skipped = []
    for node in drs.initial(name, memory_mb, vcpus):
        why = _missing_on(node, network, storage_pool, user["username"])
        if why is None:
            return {"node": node, "local": self_node.is_self(node), "skipped": skipped}
        skipped.append({"node": node, "reason": why})
    detail = "No member has the memory free for this VM while keeping its rules"
    if skipped:
        detail = "No member can take this VM: " + "; ".join(f"{s['node']}: {s['reason']}" for s in skipped)
    raise HTTPException(status_code=409, detail=detail)
