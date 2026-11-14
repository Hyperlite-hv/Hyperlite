"""The secrets vault and VM identities (docs/design/governance.md, sections 8 and 9)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import vault, vm_identity
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role, require_vm_privilege

router = APIRouter(tags=["vault"])


def _answer(fn, *args):
    try:
        return fn(*args)
    except vault.VaultError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    value: str | None = Field(None, max_length=65536)
    description: str = Field("", max_length=500)
    readers: list[str] = Field(default_factory=list, max_length=200)  # "user:<name>" or "group:<id>"


def _readers(raw):
    out = []
    for r in raw:
        kind, _, ident = r.partition(":")
        if kind not in ("user", "group") or not ident:
            raise HTTPException(status_code=422, detail=f"A reader is written user:<name> or group:<id>, not {r}")
        out.append((kind, ident))
    return out


@router.get("/vault")
def list_secrets(user: dict = Depends(get_current_user)):
    """The secrets the account may read (an administrator: all), without their values."""
    return listing([s for s in vault.secrets() if vault.may_read(user, s)])


@router.post("/vault", status_code=201)
def create_secret(payload: SecretIn, user: dict = Depends(require_role("admin"))):
    readers = _readers(payload.readers)
    secret = _answer(vault.put, payload.name, payload.value, payload.description, user["username"])
    vault.set_readers(secret["id"], readers)
    log_action(user["username"], "vault_create", payload.name, "success")
    return next(s for s in vault.secrets() if s["id"] == secret["id"])


@router.put("/vault/{secret_id}")
def update_secret(secret_id: str, payload: SecretIn, user: dict = Depends(require_role("admin"))):
    """An empty value keeps the current one."""
    readers = _readers(payload.readers)
    _answer(vault.put, payload.name, payload.value, payload.description, user["username"], secret_id)
    vault.set_readers(secret_id, readers)
    log_action(user["username"], "vault_update", payload.name, "success", "value changed" if payload.value else None)
    return next(s for s in vault.secrets() if s["id"] == secret_id)


@router.delete("/vault/{secret_id}")
def delete_secret(secret_id: str, user: dict = Depends(require_role("admin"))):
    if not vault.delete(secret_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "vault_delete", secret_id, "success")
    return {"message": "Secret deleted"}


@router.post("/vault/reveal/{name}")
def reveal(name: str, user: dict = Depends(get_current_user)):
    """The value, for an account that may read it: journaled."""
    return {"name": name, "value": _answer(vault.reveal, user, name)}


# ---- VM identities ----


class IdentityIn(BaseModel):
    role: str = Field(min_length=1, max_length=128)
    resource_type: str = Field(min_length=1, max_length=32)
    resource_id: str = Field("*", min_length=1, max_length=256)
    shell: Literal["sh", "powershell"] = "sh"


@router.get("/vms/{name}/identity")
def get_identity(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    found = vm_identity.of(name)
    return {"enabled": bool(found), **(found or {}), "path": vm_identity.PATHS.get((found or {}).get("shell", "sh"))}


@router.put("/vms/{name}/identity")
def put_identity(name: str, payload: IdentityIn, user: dict = Depends(require_vm_privilege("vm.exec"))):
    """Turn the VM's identity on, or change its role: a token written into the guest by its agent (vm.exec, since it
    writes in the guest), owned by the caller and never wider than the caller's rights."""
    if not perm.role_exists(payload.role) or payload.resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail="Name a known role and kind of perimeter")
    resource_id = "*" if payload.resource_type == "cluster" else payload.resource_id
    found = vm_identity.enable(user["username"], name, payload.role, payload.resource_type, resource_id, payload.shell)
    log_action(user["username"], "vm_identity", name, "success" if not found["last_error"] else "failed",
               found["last_error"] or f"{payload.role} on {payload.resource_type}:{resource_id}")  # fmt: skip
    return {"enabled": True, **found, "path": vm_identity.PATHS[payload.shell]}


@router.delete("/vms/{name}/identity")
def delete_identity(name: str, user: dict = Depends(require_vm_privilege("vm.exec"))):
    if not vm_identity.disable(name):
        raise HTTPException(status_code=404, detail="This VM has no identity")
    log_action(user["username"], "vm_identity_off", name, "success")
    return {"enabled": False}
