"""Load balancing of the cluster (docs/design/drs.md, #334): its settings, its rules, each VM's exception, its state
and the recommended moves, and an administrator's "apply"."""

from fastapi import APIRouter, Depends, HTTPException
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
    from app.core import cluster_lead, statd

    if cluster_lead.is_leader():
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
