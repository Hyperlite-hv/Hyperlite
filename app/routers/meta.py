"""Notes and tags of VMs, containers and nodes (app/core/object_meta.py)."""

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import object_meta
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, open_lxc_conn
from app.core.permissions import has_container_privilege, has_privilege
from app.core.security import get_current_user

router = APIRouter(prefix="/meta", tags=["meta"])

# Privilege needed to read (notes) and to edit, per kind of object. Nodes: every signed-in user reads, only an
# administrator edits (a node is not an ACL resource).
_READ = {"vm": "vm.view", "container": "container.view"}
_EDIT = {"vm": "vm.options", "container": "container.options"}


class MetaUpdate(BaseModel):
    notes: str = Field(default="", max_length=object_meta.MAX_NOTES)
    tags: list[str] = Field(default_factory=list, max_length=64)


def _allowed(user, kind, name, edit):
    if user["role"] == "admin":
        return True
    if kind == "node":
        return not edit
    privilege = (_EDIT if edit else _READ)[kind]
    check = has_privilege if kind == "vm" else has_container_privilege
    return check(user, name, privilege)


def _require_exists(kind, name, node):
    """Refuse notes on an object that does not exist: a typo would otherwise leave a note nobody finds."""
    if kind == "node":
        from app.core.cluster import get_node

        if name != "local" and not get_node(name):
            raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
        return
    conn = open_conn(node) if kind == "vm" else open_lxc_conn()
    try:
        conn.lookupByName(name)
    except libvirt.libvirtError:
        label = "VM" if kind == "vm" else "Container"
        raise HTTPException(status_code=404, detail=f"{label} '{name}' not found") from None
    finally:
        conn.close()


def _kind(kind):
    if kind not in object_meta.KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown object kind '{kind}'")
    return kind


@router.get("")
def list_meta(kind: str | None = None, user: dict = Depends(get_current_user)):
    """Tags of every object (and whether it has notes), for the lists and the tag filter."""
    return object_meta.list_all(_kind(kind) if kind else None)


@router.get("/{kind}/{name}")
def get_meta(kind: str, name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    _kind(kind)
    if not _allowed(user, kind, name, edit=False):
        raise HTTPException(status_code=403, detail="Insufficient rights on this object")
    return object_meta.get(kind, name, node)


@router.put("/{kind}/{name}")
def put_meta(
    kind: str, name: str, payload: MetaUpdate, node: str | None = None, user: dict = Depends(get_current_user)
):
    _kind(kind)
    if not _allowed(user, kind, name, edit=True):
        raise HTTPException(status_code=403, detail="Insufficient rights on this object")
    if kind == "container" and node and node != "local":
        raise HTTPException(status_code=422, detail="Containers are managed on this host only")
    _require_exists(kind, name, node)
    try:
        saved = object_meta.put(kind, name, payload.notes, payload.tags, node)
    except object_meta.MetaError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], f"update_{kind}_notes", name, "succes", f"tags: {', '.join(saved['tags']) or '-'}")
    return saved
