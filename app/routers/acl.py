from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_role
from app.core.audit import log_action
from app.core import permissions as perm

router = APIRouter(prefix="/acl", tags=["acl"])


class AclCreate(BaseModel):
    subject_type: str  # "user" | "group"
    subject_id: str    # username, ou id de groupe (en texte)
    role: str           # "lecteur" | "operateur" | "gestionnaire" | "custom:<id>"
    resource_type: str  # "vm" | "pool" | "container" (container : backlog 2026-09-18, pas de regroupement par pool pour l'instant)
    resource_id: str    # nom de VM/conteneur, ou id de pool (en texte)


class CustomRoleCreate(BaseModel):
    name: str
    privileges: list[str]


@router.get("/roles")
def get_roles_catalog(user: dict = Depends(require_role("admin"))):
    """Catalogue des roles predefinis (scopes attribuables), pour construire
    le formulaire d'attribution cote dashboard sans dupliquer la liste en
    dur. Les roles personnalises sont exposes separement (GET
    /acl/custom-roles) -- le dashboard fusionne les deux pour le selecteur."""
    return perm.ROLES


@router.get("/privileges")
def get_privileges_catalog(user: dict = Depends(require_role("admin"))):
    """Catalogue complet des privileges (cle -> libelle), pour construire le
    constructeur de role personnalise (cases a cocher)."""
    return perm.ALL_PRIVILEGES


@router.get("/custom-roles")
def list_custom_roles(user: dict = Depends(require_role("admin"))):
    return perm.list_custom_roles()


@router.post("/custom-roles", status_code=201)
def create_custom_role(payload: CustomRoleCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nom de rôle requis")
    try:
        role_id = perm.create_custom_role(name, payload.privileges)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=422, detail=f"Un rôle nommé '{name}' existe déjà")
    log_action(user["username"], "create_custom_role", f"{name} ({','.join(payload.privileges)})", "succes")
    return {"id": role_id, "key": f"custom:{role_id}", "name": name, "privileges": payload.privileges}


@router.delete("/custom-roles/{role_id}")
def delete_custom_role(role_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_custom_role(role_id)
    log_action(user["username"], "delete_custom_role", str(role_id), "succes")
    return {"message": "Rôle supprimé"}


@router.get("")
def list_acl(user: dict = Depends(require_role("admin"))):
    return perm.list_acl()


@router.post("", status_code=201)
def create_acl(payload: AclCreate, user: dict = Depends(require_role("admin"))):
    if payload.subject_type not in ("user", "group"):
        raise HTTPException(status_code=422, detail="subject_type doit être 'user' ou 'group'")
    if payload.resource_type not in ("vm", "pool", "container"):
        raise HTTPException(status_code=422, detail="resource_type doit être 'vm', 'pool' ou 'container'")
    if not perm.role_exists(payload.role):
        raise HTTPException(status_code=422, detail=f"Rôle inconnu : {payload.role}")
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
    return {"message": "Attribution supprimée"}
