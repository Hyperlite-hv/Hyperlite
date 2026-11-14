import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import guardrails, protections, renaming
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.listing import item, listing
from app.core.security import require_role
from app.domain.common import AlreadyExists

router = APIRouter(prefix="/pools", tags=["pools"])


def _refuse_protected(pool_id, deleting):
    """423 when the pool is kept from deletion (deleting it) or frozen (any change to it)."""
    refusal = protections.refusal("pool", pool_id, deleting)
    if refusal:
        raise HTTPException(status_code=423, detail=refusal)


def _size_of(vm_name):
    """The VM's vCPUs, memory and disk when this node hosts it, for the pool's envelope; nothing otherwise."""
    import libvirt

    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        domain = conn.lookupByName(vm_name)
        info = domain.info()
        return {"vcpus": info[3], "memory_mb": info[1] // 1024, "disk_gb": guardrails._disk_gb(domain)}
    except libvirt.libvirtError:
        return {}
    finally:
        conn.close()


class PoolCreate(BaseModel):
    name: str
    description: str = ""


class PoolMemberAdd(BaseModel):
    vm_name: str


@router.get("")
def list_pools(user: dict = Depends(require_role("admin"))):
    return listing(perm.list_pools())


@router.post("", status_code=201)
def create_pool(payload: PoolCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Pool name required")
    try:
        pool_id = perm.create_pool(name, payload.description)
    except (AlreadyExists, sqlite3.IntegrityError):
        # Only the name taken: another failure (a locked database, the daemon down) is its own error, never
        # "already exists".
        raise HTTPException(status_code=422, detail=f"A pool named '{name}' already exists") from None
    log_action(user["username"], "create_pool", name, "success")
    return {"id": pool_id, "name": name, "description": payload.description, "vms": []}


@router.get("/{pool_id}")
def get_pool(pool_id: int, user: dict = Depends(require_role("admin"))):
    """One pool, as GET /pools lists it."""
    return item(perm.list_pools(), "id", pool_id, f"Pool {pool_id}")


class PoolRename(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


@router.patch("/{pool_id}")
def rename_pool(pool_id: int, payload: PoolRename, user: dict = Depends(require_role("admin"))):
    """Its name, its description, or both: the pool (its members and permissions) is known by its id everywhere
    else."""
    if payload.description is not None:
        if not perm.set_pool_description(pool_id, payload.description):
            raise HTTPException(status_code=404, detail="Not found")
        log_action(user["username"], "update_pool", str(pool_id), "success", "description")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name required")
        try:
            old = renaming.rename_label("pool", pool_id, name)
        except renaming.LabelTaken as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        if old is None:
            raise HTTPException(status_code=404, detail="Not found")
        log_action(user["username"], "rename_pool", old, "success", f"-> {name}")
    elif payload.description is None:
        raise HTTPException(status_code=422, detail="Nothing to change: give a name or a description")
    pool = item(perm.list_pools(), "id", pool_id, f"Pool {pool_id}")
    return {"id": pool_id, "name": pool["name"], "description": pool["description"]}


@router.delete("/{pool_id}")
def delete_pool(pool_id: int, user: dict = Depends(require_role("admin"))):
    _refuse_protected(pool_id, deleting=True)
    perm.delete_pool(pool_id)
    protections.forget("pool", pool_id)
    guardrails.forget_pool(pool_id)
    log_action(user["username"], "delete_pool", str(pool_id), "success")
    return {"message": "Pool deleted"}


@router.post("/{pool_id}/members", status_code=201)
def add_member(pool_id: int, payload: PoolMemberAdd, user: dict = Depends(require_role("admin"))):
    _refuse_protected(pool_id, deleting=False)
    broken = guardrails.envelope_refusal(pool_id, vms=1, **_size_of(payload.vm_name))
    if broken:
        raise HTTPException(status_code=422, detail=broken)
    perm.add_pool_member(pool_id, payload.vm_name)
    log_action(user["username"], "add_pool_member", f"{pool_id}:{payload.vm_name}", "success")
    return {"message": "VM added to the pool"}


@router.delete("/{pool_id}/members/{vm_name}")
def remove_member(pool_id: int, vm_name: str, user: dict = Depends(require_role("admin"))):
    _refuse_protected(pool_id, deleting=False)
    perm.remove_pool_member(pool_id, vm_name)
    log_action(user["username"], "remove_pool_member", f"{pool_id}:{vm_name}", "success")
    return {"message": "VM removed from the pool"}
