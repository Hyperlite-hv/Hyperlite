"""A VM's hook script (docs/design/hook-scripts.md): the script attached, its last runs, and the library of the VM's
node to pick from. Attaching runs code as root at the VM's every start and stop: administrators only, as Proxmox VE
keeps it to root@pam."""

import subprocess

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import hookscript
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.security import require_role, require_vm_privilege
from app.routers.vms._shared import router


class HookScriptChoice(BaseModel):
    script: str | None = Field(None, max_length=100)


def _domain(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None


@router.get("/{name}/hookscript")
def get_vm_hookscript(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    """The VM's script, its last runs on its node and, for an administrator, the scripts of that node's library."""
    conn = open_conn(node)
    try:
        script = hookscript.get_script(_domain(conn, name))
    finally:
        conn.close()
    try:
        runs = hookscript.runs_on(node, name)[-20:]
        library = hookscript.list_scripts_on(node) if user["role"] == "admin" else None
    except (hookscript.HookError, OSError, ValueError, subprocess.SubprocessError) as e:
        raise HTTPException(status_code=502, detail=f"The node's hook scripts could not be read: {e}") from None
    return {"script": script, "runs": runs, "library": library}


@router.put("/{name}/hookscript")
def set_vm_hookscript(
    name: str, payload: HookScriptChoice, node: str | None = None, user: dict = Depends(require_role("admin"))
):
    """Attach a script of the VM's node library to the VM, or detach its script (null)."""
    script = payload.script or None
    try:
        if script:
            hookscript.check_on(node, script)
    except hookscript.HookError as e:
        log_action(user["username"], "set_vm_hookscript", name, "failed", str(e))
        raise HTTPException(status_code=422, detail=str(e)) from None
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise HTTPException(status_code=502, detail=f"The node's hook scripts could not be read: {e}") from None
    conn = open_conn(node)
    try:
        domain = _domain(conn, name)
        try:
            hookscript.set_script(domain, script)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_hookscript", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"The hook script could not be set: {msg}") from e
    finally:
        conn.close()
    log_action(user["username"], "set_vm_hookscript", name, "success", script or "none")
    return {"script": script}
