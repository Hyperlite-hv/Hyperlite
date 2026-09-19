from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import permissions as perm
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/acl", tags=["acl"])


class AclCreate(BaseModel):
    subject_type: str  # "user" | "group"
    subject_id: str  # username, or group id (as text)
    role: str  # "lecteur" | "operateur" | "gestionnaire" | "custom:<id>"
    resource_type: str  # "vm" | "pool" | "container" (containers are not grouped by pool for now)
    resource_id: str  # VM/container name, or pool id (as text)


class CustomRoleCreate(BaseModel):
    name: str
    privileges: list[str]


@router.get("/roles")
def get_roles_catalog(user: dict = Depends(require_role("admin"))):
    """Catalog of predefined roles (assignable scopes), used to build the
    assignment form in the dashboard without duplicating a hard-coded list.
    Custom roles are exposed separately (GET /acl/custom-roles); the dashboard
    merges both for the selector."""
    return perm.ROLES


@router.get("/privileges")
def get_privileges_catalog(user: dict = Depends(require_role("admin"))):
    """Full catalog of privileges (key -> label), used to build the custom role
    builder (checkboxes)."""
    return perm.ALL_PRIVILEGES


@router.get("/custom-roles")
def list_custom_roles(user: dict = Depends(require_role("admin"))):
    return perm.list_custom_roles()


@router.post("/custom-roles", status_code=201)
def create_custom_role(payload: CustomRoleCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Role name required")
    try:
        role_id = perm.create_custom_role(name, payload.privileges)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception:
        raise HTTPException(status_code=422, detail=f"A role named '{name}' already exists") from None
    log_action(user["username"], "create_custom_role", f"{name} ({','.join(payload.privileges)})", "succes")
    return {"id": role_id, "key": f"custom:{role_id}", "name": name, "privileges": payload.privileges}


@router.delete("/custom-roles/{role_id}")
def delete_custom_role(role_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_custom_role(role_id)
    log_action(user["username"], "delete_custom_role", str(role_id), "succes")
    return {"message": "Role deleted"}


@router.get("")
def list_acl(user: dict = Depends(require_role("admin"))):
    return perm.list_acl()


@router.post("", status_code=201)
def create_acl(payload: AclCreate, user: dict = Depends(require_role("admin"))):
    if payload.subject_type not in ("user", "group"):
        raise HTTPException(status_code=422, detail="subject_type must be 'user' or 'group'")
    if payload.resource_type not in ("vm", "pool", "container"):
        raise HTTPException(status_code=422, detail="resource_type must be 'vm', 'pool' or 'container'")
    if not perm.role_exists(payload.role):
        raise HTTPException(status_code=422, detail=f"Unknown role: {payload.role}")
    acl_id = perm.create_acl(
        payload.subject_type,
        payload.subject_id,
        payload.role,
        payload.resource_type,
        payload.resource_id,
    )
    log_action(
        user["username"],
        "create_acl",
        f"{payload.subject_type}:{payload.subject_id} -> {payload.role} @ {payload.resource_type}:{payload.resource_id}",
        "succes",
    )
    return {"id": acl_id}


@router.delete("/{acl_id}")
def delete_acl(acl_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_acl(acl_id)
    log_action(user["username"], "delete_acl", str(acl_id), "succes")
    return {"message": "Assignment deleted"}
