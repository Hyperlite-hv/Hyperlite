"""Protections, envelopes and guardrails (docs/design/access-2.md, sections 4 and 5)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import guardrails, protections
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user

router = APIRouter(tags=["protections"])

Kind = Literal["vm", "container", "pool", "node"]
Level = Literal["keep", "freeze"]


def _refuse_unless_steward(user, resource_type, resource_id):
    """An administrator, or access.manage on the object or a perimeter above it."""
    if not perm.has_scoped_privilege(user, resource_type, resource_id, "access.manage"):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.manage)")


def _refuse_unless_cluster_steward(user):
    if not perm.has_scoped_privilege(user, "cluster", "*", "access.manage"):
        raise HTTPException(
            status_code=403, detail="Insufficient rights on the cluster (required privilege: access.manage)"
        )


class ProtectionPut(BaseModel):
    reason: str = Field("", max_length=500)


@router.get("/protections")
def list_protections(user: dict = Depends(get_current_user)):
    return listing(protections.list_all())


@router.get("/protections/{resource_type}/{resource_id}")
def object_protections(resource_type: Kind, resource_id: str, user: dict = Depends(get_current_user)):
    """The protections that hold on that object: its own and those of its pools and node."""
    return listing(protections.holding(resource_type, resource_id))


@router.put("/protections/{resource_type}/{resource_id}/{level}")
def put_protection(
    resource_type: Kind, resource_id: str, level: Level, payload: ProtectionPut, user: dict = Depends(get_current_user)
):
    _refuse_unless_steward(user, resource_type, resource_id)
    row = protections.put(resource_type, resource_id, level, payload.reason.strip(), user["username"])
    log_action(user["username"], f"protection_{level}", f"{resource_type}:{resource_id}", "success", payload.reason)
    return row


@router.delete("/protections/{resource_type}/{resource_id}/{level}")
def lift_protection(resource_type: Kind, resource_id: str, level: Level, user: dict = Depends(get_current_user)):
    _refuse_unless_steward(user, resource_type, resource_id)
    if not protections.lift(resource_type, resource_id, level):
        raise HTTPException(status_code=404, detail="No such protection")
    log_action(user["username"], "protection_lifted", f"{resource_type}:{resource_id}", "success", level)
    return {"message": "Protection lifted"}


class Envelope(BaseModel):
    max_vms: int | None = Field(None, ge=0, le=100000)
    max_vcpus: int | None = Field(None, ge=0, le=1000000)
    max_memory_mb: int | None = Field(None, ge=0, le=1024**3)
    max_disk_gb: int | None = Field(None, ge=0, le=1024**3)


class Rule(BaseModel):
    name_pattern: str | None = Field(None, max_length=200)
    max_vcpu: int | None = Field(None, ge=1, le=4096)
    max_memory_mb: int | None = Field(None, ge=1, le=1024**3)
    max_disk_gb: int | None = Field(None, ge=1, le=1024**3)
    storage_pools: list[str] | None = Field(None, max_length=200)
    networks: list[str] | None = Field(None, max_length=200)


def _pool_or_404(pool_id):
    if pool_id not in {p["id"] for p in perm.list_pools()}:
        raise HTTPException(status_code=404, detail=f"Pool {pool_id} not found")


@router.get("/guardrails")
def get_guardrails(user: dict = Depends(get_current_user)):
    """The creation rules (by scope: "cluster", "pool:<id>") and the pools' envelopes with what each holds."""
    envelopes = guardrails.envelopes()
    pools = [
        {"id": p["id"], "name": p["name"], "envelope": envelopes.get(str(p["id"])), "usage": guardrails.usage(p["id"])}
        for p in perm.list_pools()
    ]
    return {"rules": guardrails.rules(), "pools": pools}


@router.put("/guardrails/rules/{scope}")
def put_rule(scope: str, payload: Rule, user: dict = Depends(get_current_user)):
    _refuse_unless_cluster_steward(user)
    if scope.startswith("pool:") and scope[5:].isdigit():
        _pool_or_404(int(scope[5:]))
    try:
        rule = guardrails.set_rule(scope, payload.model_dump(), user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "guardrail_set", scope, "success")
    return rule or {}


@router.put("/guardrails/envelopes/{pool_id}")
def put_envelope(pool_id: int, payload: Envelope, user: dict = Depends(get_current_user)):
    _refuse_unless_cluster_steward(user)
    _pool_or_404(pool_id)
    env = guardrails.set_envelope(pool_id, payload.model_dump(), user["username"])
    log_action(user["username"], "envelope_set", str(pool_id), "success")
    return {"envelope": env, "usage": guardrails.usage(pool_id)}
