"""The recycle bin of deleted VMs (app/core/recycle_bin.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import permissions as perm
from app.core import privileged, recycle_bin, vm_locks
from app.core.audit import log_action
from app.core.security import get_current_user, require_role
from app.daemon.registry import DaemonError

router = APIRouter(prefix="/recycle-bin", tags=["recycle-bin"])


def _held_or_404(name, user):
    if not recycle_bin.holds(name):
        raise HTTPException(status_code=404, detail=f"VM '{name}' is not in the recycle bin")
    if not perm.has_privilege(user, name, "vm.allocate"):
        raise HTTPException(
            status_code=403, detail=f"Insufficient rights on VM '{name}' (required privilege: vm.allocate)"
        )


@router.get("")
def list_bin(user: dict = Depends(get_current_user)):
    """The VMs in the bin that the account may bring back (vm.allocate on them; an administrator all), and how long
    the bin keeps them."""
    return {
        "days": recycle_bin.days(),
        "items": [i for i in recycle_bin.items() if perm.has_privilege(user, i["vm_name"], "vm.allocate")],
    }


@router.post("/{name}/restore")
def restore(name: str, user: dict = Depends(get_current_user)):
    _held_or_404(name, user)
    with vm_locks.claim_or_409(name, "a restore from the recycle bin"):
        try:
            recycle_bin.restore(name, user["username"])
        except DaemonError as e:
            raise privileged.http_error(e) from None
    log_action(user["username"], "restore_vm_from_bin", name, "success")
    return {"message": f"VM '{name}' restored"}


@router.delete("/{name}")
def purge(name: str, user: dict = Depends(get_current_user)):
    """Delete it for good now: its disks go."""
    _held_or_404(name, user)
    with vm_locks.claim_or_409(name, "emptying the recycle bin"):
        try:
            kept = recycle_bin.purge(name, user["username"])
        except DaemonError as e:
            raise privileged.http_error(e) from None
    log_action(user["username"], "delete_vm", name, "success", "Emptied from the recycle bin")
    return {"message": f"VM '{name}' deleted", "kept_disks": kept}


class BinSettings(BaseModel):
    days: int = Field(ge=0, le=365)


@router.put("/settings")
def set_settings(payload: BinSettings, user: dict = Depends(require_role("admin"))):
    """0 turns the bin off: a deletion deletes at once, as before."""
    recycle_bin.set_days(payload.days)
    log_action(user["username"], "recycle_bin_settings", str(payload.days), "success")
    return {"days": recycle_bin.days()}
