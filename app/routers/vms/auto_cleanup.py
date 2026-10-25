import logging

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_vm_privilege
from app.core.vm_meta import (
    delete_vm_auto_cleanup,
    get_vm_auto_cleanup,
    set_vm_auto_cleanup,
)
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


class AutoCleanupConfig(BaseModel):
    inactive_days: int = Field(ge=1, le=365)


@router.get("/{name}/auto-cleanup")
def get_vm_auto_cleanup_route(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    config = get_vm_auto_cleanup(name)
    if not config:
        return {"active": False}
    return {"active": True, **config}


@router.put("/{name}/auto-cleanup")
def set_vm_auto_cleanup_route(
    name: str, payload: AutoCleanupConfig, user: dict = Depends(require_vm_privilege("vm.hardware"))
):
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_auto_cleanup", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    set_vm_auto_cleanup(name, payload.inactive_days)
    log_action(user["username"], "set_auto_cleanup", name, "success", f"threshold {payload.inactive_days} day(s)")
    return {"message": f"Automatic cleanup enabled ({payload.inactive_days} day(s) of inactivity)"}


@router.delete("/{name}/auto-cleanup")
def disable_vm_auto_cleanup_route(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    delete_vm_auto_cleanup(name)
    log_action(user["username"], "disable_auto_cleanup", name, "success")
    return {"message": "Automatic cleanup disabled"}
