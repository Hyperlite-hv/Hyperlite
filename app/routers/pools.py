from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_role
from app.core.audit import log_action
from app.core import permissions as perm

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
        raise HTTPException(status_code=422, detail="Nom de pool requis")
    try:
        pool_id = perm.create_pool(name, payload.description)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Un pool nomme '{name}' existe deja")
    log_action(user["username"], "create_pool", name, "succes")
    return {"id": pool_id, "name": name, "description": payload.description, "vms": []}


@router.delete("/{pool_id}")
def delete_pool(pool_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_pool(pool_id)
    log_action(user["username"], "delete_pool", str(pool_id), "succes")
    return {"message": "Pool supprime"}


@router.post("/{pool_id}/members", status_code=201)
def add_member(pool_id: int, payload: PoolMemberAdd, user: dict = Depends(require_role("admin"))):
    perm.add_pool_member(pool_id, payload.vm_name)
    log_action(user["username"], "add_pool_member", f"{pool_id}:{payload.vm_name}", "succes")
    return {"message": "VM ajoutee au pool"}


@router.delete("/{pool_id}/members/{vm_name}")
def remove_member(pool_id: int, vm_name: str, user: dict = Depends(require_role("admin"))):
    perm.remove_pool_member(pool_id, vm_name)
    log_action(user["username"], "remove_pool_member", f"{pool_id}:{vm_name}", "succes")
    return {"message": "VM retiree du pool"}
