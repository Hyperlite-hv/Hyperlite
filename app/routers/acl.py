from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import renaming
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


class CustomRoleRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/custom-roles/{role_id}")
def rename_custom_role(role_id: int, payload: CustomRoleRename, user: dict = Depends(require_role("admin"))):
    """Only its name changes: the role (custom:<id> in the assignments) is known by its id everywhere else."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name required")
    try:
        old = renaming.rename_label("custom_role", role_id, name)
    except renaming.LabelTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if old is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "rename_custom_role", old, "succes", f"-> {name}")
    return {"id": role_id, "name": name}


@router.delete("/custom-roles/{role_id}")
def delete_custom_role(role_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_custom_role(role_id)
    log_action(user["username"], "delete_custom_role", str(role_id), "succes")
    return {"message": "Role deleted"}


@router.get("/object/{resource_type}/{resource_id}")
def object_acl(resource_type: str, resource_id: str, user: dict = Depends(require_role("admin"))):
    """Assignments on one VM or container, for the object's Permissions tab: its own ones and, for a VM, the
    ones it inherits from its pools."""
    if resource_type not in ("vm", "container"):
        raise HTTPException(status_code=422, detail="resource_type must be 'vm' or 'container'")
    return perm.acl_for_object(resource_type, resource_id)


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
