from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_role
from app.core.audit import log_action
from app.core import permissions as perm

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupCreate(BaseModel):
    name: str


class MemberAdd(BaseModel):
    username: str


@router.get("")
def list_groups(user: dict = Depends(require_role("admin"))):
    return perm.list_groups()


@router.post("", status_code=201)
def create_group(payload: GroupCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nom de groupe requis")
    try:
        group_id = perm.create_group(name)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Un groupe nommé '{name}' existe déjà")
    log_action(user["username"], "create_group", name, "succes")
    return {"id": group_id, "name": name, "membres": []}


@router.delete("/{group_id}")
def delete_group(group_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_group(group_id)
    log_action(user["username"], "delete_group", str(group_id), "succes")
    return {"message": "Groupe supprimé"}


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: int, payload: MemberAdd, user: dict = Depends(require_role("admin"))):
    perm.add_group_member(group_id, payload.username)
    log_action(user["username"], "add_group_member", f"{group_id}:{payload.username}", "succes")
    return {"message": "Membre ajouté"}


@router.delete("/{group_id}/members/{username}")
def remove_member(group_id: int, username: str, user: dict = Depends(require_role("admin"))):
    perm.remove_group_member(group_id, username)
    log_action(user["username"], "remove_group_member", f"{group_id}:{username}", "succes")
    return {"message": "Membre retiré"}
