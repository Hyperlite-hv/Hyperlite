"""hyperlite-admin's lockdown (app/core/admin_cli.py), set from Administration > API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import admin_cli
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/admin-cli", tags=["admin-cli"])


@router.get("")
def get_admin_cli(user: dict = Depends(require_role("admin"))):
    """lockdown: whether root of a node is refused its own session (root@<node>), as ESXi's lockdown mode."""
    return {"lockdown": admin_cli.locked()}


class AdminCliSettings(BaseModel):
    lockdown: bool


@router.put("")
def put_admin_cli(payload: AdminCliSettings, user: dict = Depends(require_role("admin"))):
    lockdown = admin_cli.set_locked(payload.lockdown)
    log_action(user["username"], "admin_cli_lockdown", "api", "success", "on" if lockdown else "off")
    return {"lockdown": lockdown}
