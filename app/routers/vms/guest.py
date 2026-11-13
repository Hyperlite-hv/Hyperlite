"""Inside a VM, through its guest agent (#348): what its OS says about itself, and a command run there.

Running a command is as strong as an administrator's console in the guest (the agent runs as root or SYSTEM): it needs
the vm.exec privilege, which no predefined role holds, and every run is written to the journal with its shell, the size and
fingerprint of its script (never the script itself: it may hold a secret) and its exit code."""

import hashlib
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from app.core import privileged
from app.core.audit import log_action
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


class GuestRun(BaseModel):
    shell: Literal["sh", "bash", "powershell", "cmd"] = "sh"
    script: str = Field(min_length=1, max_length=16384)
    timeout_s: int = Field(30, ge=1, le=120)


def _remote(node):
    return node if node and node != "local" else None


@router.get("/{name}/guest")
def get_guest_info(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    """OS, kernel, host name, time zone, signed-in users and file systems, as the guest agent reports them. 422 when
    the VM is stopped or has no running agent."""
    return privileged.ask("vm.agent_info", user, "guest_info", name, vm=name, node=_remote(node))


@router.post("/{name}/guest/run")
def run_in_guest(
    name: str, payload: GuestRun, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.exec"))
):
    """Runs a script in the guest's shell and waits for it (at most timeout_s): its exit code, output and errors (64 KiB
    of each at most). A script still running at the deadline keeps running in the guest and is reported as timed out."""
    digest = hashlib.sha256(payload.script.encode()).hexdigest()[:12]
    result = privileged.ask(
        "vm.agent_exec",
        user,
        "guest_run",
        name,
        vm=name,
        node=_remote(node),
        shell=payload.shell,
        script=payload.script,
        timeout_s=payload.timeout_s,
        timeout=payload.timeout_s + 15,
    )
    outcome = "timed out" if result["timed_out"] else f"exit {result['exit_code']}"
    log_action(
        user["username"],
        "guest_run",
        name,
        "success" if result["exit_code"] == 0 else "failed",
        f"{payload.shell}, {len(payload.script)} characters, sha256 {digest}: {outcome}",
    )
    return result
