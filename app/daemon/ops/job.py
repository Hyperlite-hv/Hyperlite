"""The automation jobs' steps (app/core/jobs.py, docs/design/privilege-separation.md, section 13): a step runs a
command as root on this host, or over SSH with the automation key on one of its VMs, which only root reads. A job's
command is arbitrary code by design, an administrator's, as the host shell is (section 8, decision 3): the API decides
who may run one, the daemon runs it, and reads the VM's address and account itself, never from the API."""

import contextlib
import os
import signal
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
    # Its own process group: a timeout or a cancel stops what the step started in the background too. Killing the
    # shell alone left a grandchild holding the pipes, and the run never ended.
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", start_new_session=True
    )
    with _lock:
        _running[args.task_id] = proc
    try:
        out, err = proc.communicate(timeout=STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        _drain(proc)
        return {"stdout": "", "stderr": f"Timed out after {STEP_TIMEOUT_S}s", "exit": 124}
    finally:
        with _lock:
            _running.pop(args.task_id, None)
    return {"stdout": _output(out), "stderr": _output(err), "exit": proc.returncode}


def _signal_group(proc, sig):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


def _drain(proc):
    """What is left of a killed step's output, never waited for longer than a few seconds."""
    try:
        return proc.communicate(timeout=GRACE_S)
    except subprocess.TimeoutExpired:
        for pipe in (proc.stdout, proc.stderr):
            with contextlib.suppress(OSError):
                pipe.close()
        return "", ""


# What a stopped step's processes get before their pipes are closed.
GRACE_S = 5


class CancelArgs(Args):
    task_id: str = Field(min_length=1, max_length=64)


@operation("job.cancel", CancelArgs)
def cancel(args, ctx):
    """Stop the step a run's task is waiting for: {"cancelled"}."""
    with _lock:
        proc = _running.get(args.task_id)
    if proc is None or proc.poll() is not None:
        return {"cancelled": False}
    _signal_group(proc, signal.SIGTERM)
    return {"cancelled": True}


# ---- Kubernetes clusters (app/core/k8s_cluster.py): their VMs set up over SSH with the automation key ----

VM_SCRIPT_TIMEOUT_S = 900


class VmScriptArgs(Args):
    vm: str = Field(min_length=1, max_length=64)
    script: str = Field(min_length=1, max_length=262144)
    secret: str = Field(default="", max_length=4096, description="Read by the script into $HL_SECRET, never shown")
    timeout_s: int = Field(default=STEP_TIMEOUT_S, ge=1, le=VM_SCRIPT_TIMEOUT_S)


class VmCopyArgs(Args):
    vm: str = Field(min_length=1, max_length=64)
    files: list[str] = Field(min_length=1, max_length=8)
    remote_dir: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")


@operation("job.vm_script", VmScriptArgs)
def vm_script(args, ctx):
    """A bash script run in a VM of this node as its account: the secret is the first line of its standard input, the
    script the rest, so neither shows on a command line. {"stdout", "stderr", "exit"}; exit 124 on a timeout."""
    argv = _vm_ssh(args.vm, "bash -c 'read -r HL_SECRET; export HL_SECRET; exec bash -s'")
    try:
        r = subprocess.run(
            argv, input=f"{args.secret}\n{args.script}", capture_output=True, text=True, timeout=args.timeout_s
        )
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {args.timeout_s}s", "exit": 124}
    return {"stdout": _output(r.stdout), "stderr": _output(r.stderr), "exit": r.returncode}


@operation("job.vm_copy", VmCopyArgs)
def vm_copy(args, ctx):
    """Files Hyperlite downloaded for Kubernetes (under its k3s directory, nowhere else) copied into a directory of
    the VM account's home."""
    from pathlib import Path

    from app.core import paths

    root = Path(paths.K3S).resolve()
    sources = []
    for name in args.files:
        path = Path(name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DaemonError("invalid_arguments", f"Not a file of the Kubernetes downloads: {name}")
        sources.append(path)
    for path in sources:
        target = f"{args.remote_dir}/{path.name}"
        argv = _vm_ssh(args.vm, f"mkdir -p ~/{args.remote_dir} && cat > ~/{target}")
        with path.open("rb") as f:
            try:
                r = subprocess.run(argv, stdin=f, capture_output=True, timeout=VM_SCRIPT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise DaemonError("failed", f"Copy of {path.name} to {args.vm} timed out") from None
        if r.returncode != 0:
            raise DaemonError(
                "failed", f"Copy of {path.name} to {args.vm} failed: {r.stderr.decode(errors='replace')[-300:]}"
            )
    return {"copied": [p.name for p in sources]}
