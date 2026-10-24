"""A VM's hook script (docs/design/hook-scripts.md): the script attached, its last runs, and the library of the VM's
node to pick from. Attaching runs code as root at the VM's every start and stop: administrators only, as Proxmox VE
keeps it to root@pam."""

import subprocess

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import hookscript, privileged
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.security import require_role, require_vm_privilege
from app.daemon.registry import DaemonError
from app.routers.vms._shared import router


class HookScriptChoice(BaseModel):
    script: str | None = Field(None, max_length=100)


def _domain(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None


def _remote(node):
    return node if node and node != "local" else None


@router.get("/{name}/hookscript")
def get_vm_hookscript(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    """The VM's script, its last runs on its node and, for an administrator, the scripts of that node's library."""
    conn = open_conn(node, readonly=True)
    try:
        script = hookscript.get_script(_domain(conn, name))
    finally:
        conn.close()
    try:
        runs = hookscript.runs_via(_remote(node), name)[-20:]
        library = hookscript.library_via(_remote(node)) if user["role"] == "admin" else None
    except DaemonError as e:
        raise privileged.http_error(e) from None
    except hookscript.HookError as e:
        raise HTTPException(status_code=502, detail=f"The node's hook scripts could not be read: {e}") from None
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        msg = describe_exception(e)
        raise HTTPException(status_code=502, detail=f"The node's hook scripts could not be read: {msg}") from None
    return {"script": script, "runs": runs, "library": library}


@router.put("/{name}/hookscript")
def set_vm_hookscript(
    name: str, payload: HookScriptChoice, node: str | None = None, user: dict = Depends(require_role("admin"))
):
    """Attach a script of the VM's node library to the VM, or detach its script (null)."""
    # Checked again and attached by hyperlite-daemon (app/daemon/ops/vm.py), from that node's own library: a script
    # runs as root.
    script = payload.script or None
    conn = open_conn(node, readonly=True)
    try:
        _domain(conn, name)
    finally:
        conn.close()
    privileged.ask(
        "vm.set_hookscript",
        user,
        "set_vm_hookscript",
        name,
        failed="The hook script could not be set: {}",
        vm=name,
        script=script,
        node=_remote(node),
    )
    log_action(user["username"], "set_vm_hookscript", name, "success", script or "none")
    return {"script": script}
