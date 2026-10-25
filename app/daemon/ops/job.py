"""The automation jobs' steps (app/core/jobs.py, docs/design/privilege-separation.md, section 13): a step runs a
command as root on this host, or over SSH with the automation key on one of its VMs, which only root reads. A job's
command is arbitrary code by design, an administrator's, as the host shell is (section 8, decision 3): the API decides
who may run one, the daemon runs it, and reads the VM's address and account itself, never from the API."""

import subprocess
import threading
from typing import Literal

from pydantic import Field

from app.daemon.registry import Args, DaemonError, operation

# A step's limit, as before (app/core/jobs.py waits a little longer for the answer).
STEP_TIMEOUT_S = 120
# What a step may print into the run's log, each of its outputs.
OUTPUT_LIMIT = 1 << 20
TRUNCATED = "\n[output truncated]"

_running = {}  # task id -> the step's process, for job.cancel
_lock = threading.Lock()


class StepArgs(Args):
    task_id: str = Field(min_length=1, max_length=64)
    target_type: Literal["host", "vm"]
    target: str | None = Field(default=None, min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=65536)


def _vm_ssh(vm, command):
    """ssh to a running VM of this node as its account, with the automation key: the address the VM page shows (the
    guest agent's, else the DHCP lease)."""
    from app.core import console_sessions, guest_agent, libvirt_utils
    from app.core.vm_meta import get_vm_ssh_user
    from app.daemon.ops import console

    with libvirt_utils.local_conn() as conn:
        domain = console._vm(conn, vm)
        address = console._address(guest_agent.ipv4(domain) or console._lease_ipv4(domain))
    user = console._account(get_vm_ssh_user(vm), vm)
    return [*console_sessions.guest_ssh(address, user, terminal=False), command]


def _output(text):
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + TRUNCATED


@operation("job.step", StepArgs)
def step(args, ctx):
    """One step of a job's run, waited for: {"stdout", "stderr", "exit"}; exit 124 after STEP_TIMEOUT_S, as before.
    A VM that cannot be reached is refused with the reason the run's log shows."""
    if args.target_type == "host":
        argv = ["bash", "-c", args.command]
    elif args.target is None:
        raise DaemonError("invalid_arguments", "A step on a VM names the VM")
    else:
        argv = _vm_ssh(args.target, args.command)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    with _lock:
        _running[args.task_id] = proc
    try:
        out, err = proc.communicate(timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"stdout": "", "stderr": f"Timed out after {STEP_TIMEOUT_S}s", "exit": 124}
    finally:
        with _lock:
            _running.pop(args.task_id, None)
    return {"stdout": _output(out), "stderr": _output(err), "exit": proc.returncode}


class CancelArgs(Args):
    task_id: str = Field(min_length=1, max_length=64)


@operation("job.cancel", CancelArgs)
def cancel(args, ctx):
    """Stop the step a run's task is waiting for: {"cancelled"}."""
    with _lock:
        proc = _running.get(args.task_id)
    if proc is None or proc.poll() is not None:
        return {"cancelled": False}
    proc.terminate()
    return {"cancelled": True}
