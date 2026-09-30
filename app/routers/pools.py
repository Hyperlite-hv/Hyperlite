from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import renaming
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/pools", tags=["pools"])


class PoolCreate(BaseModel):
    name: str
    description: str = ""


class PoolMemberAdd(BaseModel):
    vm_name: str


@router.get("")
def list_pools(user: dict = Depends(require_role("admin"))):
    return perm.list_pools()


@router.post("", status_code=201)
def create_pool(payload: PoolCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Pool name required")
    try:
        pool_id = perm.create_pool(name, payload.description)
    except Exception:
        raise HTTPException(status_code=422, detail=f"A pool named '{name}' already exists") from None
    log_action(user["username"], "create_pool", name, "succes")
    return {"id": pool_id, "name": name, "description": payload.description, "vms": []}


class PoolRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/{pool_id}")
def rename_pool(pool_id: int, payload: PoolRename, user: dict = Depends(require_role("admin"))):
    """Only its name changes: the pool (its members and permissions) is known by its id everywhere else."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name required")
    try:
        old = renaming.rename_label("pool", pool_id, name)
    except renaming.LabelTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if old is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "rename_pool", old, "succes", f"-> {name}")
    return {"id": pool_id, "name": name}


@router.delete("/{pool_id}")
def delete_pool(pool_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_pool(pool_id)
    log_action(user["username"], "delete_pool", str(pool_id), "succes")
    return {"message": "Pool deleted"}


@router.post("/{pool_id}/members", status_code=201)
def add_member(pool_id: int, payload: PoolMemberAdd, user: dict = Depends(require_role("admin"))):
    perm.add_pool_member(pool_id, payload.vm_name)
    log_action(user["username"], "add_pool_member", f"{pool_id}:{payload.vm_name}", "succes")
    return {"message": "VM added to the pool"}


@router.delete("/{pool_id}/members/{vm_name}")
def remove_member(pool_id: int, vm_name: str, user: dict = Depends(require_role("admin"))):
    perm.remove_pool_member(pool_id, vm_name)
    log_action(user["username"], "remove_pool_member", f"{pool_id}:{vm_name}", "succes")
    return {"message": "VM removed from the pool"}
