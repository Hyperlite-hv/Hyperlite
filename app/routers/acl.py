import re
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import renaming
from app.core.audit import log_action
from app.core.listing import item, listing
from app.core.security import get_current_user, require_role
from app.domain.common import AlreadyExists
from app.services import account_service

router = APIRouter(prefix="/acl", tags=["acl"])
NODE_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")


class AclCreate(BaseModel):
    subject_type: str  # "user" | "group"
    subject_id: str  # username, or group id (as text)
    role: str  # a key of permissions.ROLES, or "custom:<id>"
    resource_type: str  # one of permissions.RESOURCE_TYPES: an object, a pool, a node or "cluster"
    resource_id: str  # VM/container/storage/network/store/node name, pool id (as text), "*" for the cluster
    # Terms (docs/design/access-2.md, sections 6 and 8): the assignment ends then; grants only on objects with that tag.
    expires_at: datetime | None = None
    tag: str | None = Field(None, max_length=32)
    reason: str | None = Field(None, max_length=500)


class CustomRoleCreate(BaseModel):
    name: str
    privileges: list[str]


@router.get("/roles")
def get_roles_catalog(user: dict = Depends(get_current_user)):
    """Catalog of predefined roles (assignable scopes), used to build the
    assignment form in the dashboard without duplicating a hard-coded list.
    Custom roles are exposed separately (GET /acl/custom-roles); the dashboard
    merges both for the selector."""
    return perm.ROLES


@router.get("/privileges")
def get_privileges_catalog(user: dict = Depends(get_current_user)):
    """Full catalog of privileges (key -> label), used to build the custom role
    builder (checkboxes)."""
    return perm.ALL_PRIVILEGES  # a catalogue {privilege: label}, not a list


@router.get("/custom-roles")
def list_custom_roles(user: dict = Depends(get_current_user)):
    """Every account may read the catalogue (a delegate assigns from it); only an administrator changes it."""
    return listing(perm.list_custom_roles())


@router.post("/custom-roles", status_code=201)
def create_custom_role(payload: CustomRoleCreate, user: dict = Depends(require_role("admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Role name required")
    try:
        role_id = perm.create_custom_role(name, payload.privileges)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (AlreadyExists, sqlite3.IntegrityError):
        # Only the name taken: another failure (a locked database, the daemon down) is its own error, never
        # "already exists".
        raise HTTPException(status_code=422, detail=f"A role named '{name}' already exists") from None
    log_action(user["username"], "create_custom_role", f"{name} ({','.join(payload.privileges)})", "success")
    return {"id": role_id, "key": f"custom:{role_id}", "name": name, "privileges": payload.privileges}


@router.get("/custom-roles/{role_id}")
def get_custom_role(role_id: int, user: dict = Depends(require_role("admin"))):
    """One custom role, as GET /acl/custom-roles lists it."""
    return item(perm.list_custom_roles(), "id", role_id, f"Role {role_id}")


class CustomRoleRename(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    privileges: list[str] | None = None


@router.patch("/custom-roles/{role_id}")
def rename_custom_role(role_id: int, payload: CustomRoleRename, user: dict = Depends(require_role("admin"))):
    """Its name, its privileges, or both. The role (custom:<id> in the assignments) is known by its id everywhere
    else, so its assignments stay: a role changed by deleting and creating it again lost every one of them."""
    if payload.privileges is not None:
        try:
            found = perm.set_custom_role_privileges(role_id, payload.privileges)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not found:
            raise HTTPException(status_code=404, detail="Not found")
        log_action(user["username"], "update_custom_role", str(role_id), "success", ",".join(payload.privileges))
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name required")
        try:
            old = renaming.rename_label("custom_role", role_id, name)
        except renaming.LabelTaken as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        if old is None:
            raise HTTPException(status_code=404, detail="Not found")
        log_action(user["username"], "rename_custom_role", old, "success", f"-> {name}")
    elif payload.privileges is None:
        raise HTTPException(status_code=422, detail="Nothing to change: give a name or privileges")
    role = item(perm.list_custom_roles(), "id", role_id, f"Role {role_id}")
    return {"id": role_id, "name": role["label"], "privileges": sorted(role["privileges"])}


@router.delete("/custom-roles/{role_id}")
def delete_custom_role(role_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_custom_role(role_id)
    log_action(user["username"], "delete_custom_role", str(role_id), "success")
    return {"message": "Role deleted"}


@router.get("/object/{resource_type}/{resource_id}")
def object_acl(resource_type: str, resource_id: str, user: dict = Depends(get_current_user)):
    """Assignments on one object, for its Rights tab: its own ones and the ones it inherits from the perimeters that
    contain it (a VM's pools, the node, the cluster). An administrator, or access.view on the object or above."""
    if resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    if not perm.may_view_access(user, resource_type, resource_id):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.view)")
    return listing(perm.acl_for_object(resource_type, resource_id))


@router.get("")
def list_acl(user: dict = Depends(require_role("admin"))):
    return listing(perm.list_acl())


@router.post("", status_code=201)
def create_acl(payload: AclCreate, user: dict = Depends(get_current_user)):
    if payload.subject_type not in ("user", "group"):
        raise HTTPException(status_code=422, detail="subject_type must be 'user' or 'group'")
    if payload.resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    if not perm.role_exists(payload.role):
        raise HTTPException(status_code=422, detail=f"Unknown role: {payload.role}")
    if payload.resource_type == "cluster":
        payload.resource_id = "*"
    if payload.resource_type == "node" and not NODE_NAME.match(payload.resource_id):
        raise HTTPException(status_code=422, detail="A node is named in lowercase letters, digits, dots and dashes")
    refusal = perm.grant_refusal(
        user, payload.subject_type, payload.subject_id, payload.role, payload.resource_type, payload.resource_id
    )
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    if payload.resource_type in ("storage", "network", "backup_store", "node", "cluster"):
        from app.core import statd

        behind = statd.members_without(
            {"backup_store": "acl_backup_store", "node": "acl_perimeters", "cluster": "acl_perimeters"}.get(
                payload.resource_type, "acl_storage_network"
            )
        )
        if behind:
            raise HTTPException(
                status_code=409,
                detail=f"{', '.join(behind)} still {'runs' if len(behind) == 1 else 'run'} a version that does not know "
                f"assignments on a {payload.resource_type.replace('_', ' ')}: assign it once every node is updated",
            )
    # A right given to a name nobody has yet would go to whoever is created under it later.
    if payload.subject_type == "user" and not account_service.get(payload.subject_id):
        raise HTTPException(status_code=404, detail=f"User '{payload.subject_id}' not found")
    if payload.subject_type == "group" and payload.subject_id not in {str(g["id"]) for g in perm.list_groups()}:
        raise HTTPException(status_code=404, detail=f"Group {payload.subject_id} not found")
    if payload.resource_type == "pool" and payload.resource_id not in {str(p["id"]) for p in perm.list_pools()}:
        raise HTTPException(status_code=404, detail=f"Pool {payload.resource_id} not found")
    if payload.expires_at is not None or payload.tag or payload.reason:
        from app.core import object_meta, statd

        if statd.members_without("acl_terms"):
            raise HTTPException(
                status_code=409, detail="Update every node before giving rights that end or carry a tag"
            )
        expires_at = None
        if payload.expires_at is not None:
            end = payload.expires_at if payload.expires_at.tzinfo else payload.expires_at.replace(tzinfo=UTC)
            if end <= datetime.now(UTC):
                raise HTTPException(status_code=422, detail="The end of the assignment has already passed")
            expires_at = end.astimezone(UTC).isoformat()
        tag = None
        if payload.tag:
            try:
                tag = object_meta.normalize_tags([payload.tag])[0]
            except object_meta.MetaError as e:
                raise HTTPException(status_code=422, detail=str(e)) from None
        acl_id = perm.create_dated_acl(
            payload.subject_type, payload.subject_id, payload.role, payload.resource_type, payload.resource_id,
            expires_at, tag, payload.reason, user["username"], "manual",
        )  # fmt: skip
    else:
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
        "success",
    )
    return {"id": acl_id}


@router.delete("/{acl_id}")
def delete_acl(acl_id: int, user: dict = Depends(get_current_user)):
    row = next((a for a in perm.list_acl() if a["id"] == acl_id), None)
    if row is not None and not (row["subject_type"] == "user" and row["subject_id"] == user["username"]):
        refusal = perm.grant_refusal(
            user, row["subject_type"], row["subject_id"], row["role"], row["resource_type"], row["resource_id"]
        )
        if refusal:
            raise HTTPException(status_code=403, detail=refusal)
    elif user["role"] != "admin":
        raise HTTPException(status_code=404, detail=f"Assignment {acl_id} not found")
    perm.delete_acl(acl_id)
    log_action(user["username"], "delete_acl", str(acl_id), "success")
    return {"message": "Assignment deleted"}


# Registered last so that the fixed paths above (/roles, /privileges, /custom-roles) keep their routes.
@router.get("/{acl_id}")
def get_acl(acl_id: int, user: dict = Depends(require_role("admin"))):
    """One assignment, as GET /acl lists it."""
    return item(perm.list_acl(), "id", acl_id, f"Assignment {acl_id}")
