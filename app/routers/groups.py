from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import renaming
from app.core.audit import log_action
from app.core.security import require_role

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
        raise HTTPException(status_code=422, detail="Group name required")
    try:
        group_id = perm.create_group(name)
    except Exception:
        raise HTTPException(status_code=422, detail=f"A group named '{name}' already exists") from None
    log_action(user["username"], "create_group", name, "succes")
    return {"id": group_id, "name": name, "membres": []}


class GroupRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/{group_id}")
def rename_group(group_id: int, payload: GroupRename, user: dict = Depends(require_role("admin"))):
    """Only its name changes: the group (its members and permissions) is known by its id everywhere else."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name required")
    try:
        old = renaming.rename_label("group", group_id, name)
    except renaming.LabelTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if old is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "rename_group", old, "succes", f"-> {name}")
    return {"id": group_id, "name": name}


@router.delete("/{group_id}")
def delete_group(group_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_group(group_id)
    log_action(user["username"], "delete_group", str(group_id), "succes")
    return {"message": "Group deleted"}


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: int, payload: MemberAdd, user: dict = Depends(require_role("admin"))):
    perm.add_group_member(group_id, payload.username)
    log_action(user["username"], "add_group_member", f"{group_id}:{payload.username}", "succes")
    return {"message": "Member added"}


@router.delete("/{group_id}/members/{username}")
def remove_member(group_id: int, username: str, user: dict = Depends(require_role("admin"))):
    perm.remove_group_member(group_id, username)
    log_action(user["username"], "remove_group_member", f"{group_id}:{username}", "succes")
    return {"message": "Member removed"}
