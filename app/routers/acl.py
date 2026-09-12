from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_role
from app.core.audit import log_action
from app.core import permissions as perm

router = APIRouter(prefix="/acl", tags=["acl"])


class AclCreate(BaseModel):
    subject_type: str  # "user" | "group"
    subject_id: str    # username, ou id de groupe (en texte)
    role: str           # "lecteur" | "operateur" | "gestionnaire"
    resource_type: str  # "vm" | "pool"
    resource_id: str    # nom de VM, ou id de pool (en texte)


@router.get("/roles")
def get_roles_catalog(user: dict = Depends(require_role("admin"))):
    """Catalogue des roles scopes attribuables, pour construire le formulaire
    d'attribution cote dashboard sans dupliquer la liste en dur."""
    return perm.ROLES


@router.get("")
def list_acl(user: dict = Depends(require_role("admin"))):
    return perm.list_acl()


@router.post("", status_code=201)
def create_acl(payload: AclCreate, user: dict = Depends(require_role("admin"))):
    if payload.subject_type not in ("user", "group"):
        raise HTTPException(status_code=422, detail="subject_type doit etre 'user' ou 'group'")
    if payload.resource_type not in ("vm", "pool"):
        raise HTTPException(status_code=422, detail="resource_type doit etre 'vm' ou 'pool'")
    if payload.role not in perm.ROLES:
        raise HTTPException(status_code=422, detail=f"Role inconnu : {payload.role}")
    acl_id = perm.create_acl(
        payload.subject_type, payload.subject_id, payload.role,
        payload.resource_type, payload.resource_id,
    )
    log_action(
        user["username"], "create_acl",
        f"{payload.subject_type}:{payload.subject_id} -> {payload.role} @ {payload.resource_type}:{payload.resource_id}",
        "succes",
    )
    return {"id": acl_id}


@router.delete("/{acl_id}")
def delete_acl(acl_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_acl(acl_id)
    log_action(user["username"], "delete_acl", str(acl_id), "succes")
    return {"message": "Attribution supprimee"}
