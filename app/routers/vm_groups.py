"""VM groups: N identical VMs from one template (app/core/vm_groups.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import vm_groups
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import require_role

router = APIRouter(prefix="/vm-groups", tags=["vm-groups"])


def _answer(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except vm_groups.GroupError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template: str = Field(min_length=1, max_length=256)
    prefix: str = Field(min_length=1, max_length=41)
    count: int = Field(0, ge=0, le=100)
    network: str | None = Field(None, max_length=256)
    linked: bool = True
    pool_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=16)


class CountIn(BaseModel):
    count: int = Field(ge=0, le=100)
    force: bool = False


@router.get("")
def list_groups(user: dict = Depends(require_role("admin"))):
    return listing(vm_groups.groups())


@router.post("", status_code=201)
def create_group(payload: GroupIn, user: dict = Depends(require_role("admin"))):
    g = _answer(vm_groups.create, payload.model_dump(), user["username"])
    log_action(user["username"], "vm_group_create", g["name"], "success", f"{payload.template}, {payload.count} VM(s)")
    done = _answer(vm_groups.scale, g["id"], payload.count, user) if payload.count else {"made": [], "removed": []}
    return {**next(x for x in vm_groups.groups() if x["id"] == g["id"]), **done}


@router.put("/{group_id}/count")
def set_count(group_id: str, payload: CountIn, user: dict = Depends(require_role("admin"))):
    done = _answer(vm_groups.scale, group_id, payload.count, user, force=payload.force)
    log_action(user["username"], "vm_group_scale", group_id, "success",
               f"to {payload.count}: +{len(done['made'])} -{len(done['removed'])}")  # fmt: skip
    return {**next(x for x in vm_groups.groups() if x["id"] == group_id), **done}


@router.delete("/{group_id}")
def delete_group(group_id: str, user: dict = Depends(require_role("admin"))):
    """The group is forgotten; its VMs stay, as ordinary VMs."""
    if not vm_groups.delete(group_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "vm_group_delete", group_id, "success")
    return {"message": "Group forgotten, its VMs kept"}
