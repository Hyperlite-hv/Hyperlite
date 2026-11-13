"""A VM's configuration history (app/core/config_history.py): its definition after every change, and the diffs."""

from fastapi import Depends, HTTPException

from app.core import config_history
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


@router.get("/{name}/config-history")
def get_config_history(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    """The recorded versions of the VM's definition, newest first, each with the journal's actions since the one
    before it. node: the VM's node, as for GET /vms (omitted or 'local' = this host)."""
    return {"items": config_history.versions(config_history.target_of(name, node))}


@router.get("/{name}/config-history/{version_id}")
def get_config_version(
    name: str, version_id: int, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))
):
    """One version: its XML and the unified diff against the version before it."""
    found = config_history.diff(config_history.target_of(name, node), version_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such version of this VM's configuration")
    return found
