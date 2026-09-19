"""Interactive shell directly on the physical host (the server running
Hyperlite), the equivalent of vSphere's DCUI/ESXi Shell but a full shell rather
than a restricted menu.

SECURITY WARNING: hyperlite.service runs as root (see the systemd unit file, no
`User=`), so this shell is full root access to the physical machine, strictly
more sensitive than the existing per-VM SSH terminal (itself already limited to
admins for the same reason). There is no sandboxing or command allowlist here:
the only barriers are access control (admins only), a short-lived single-use
ticket (like the other consoles), and full traceability (every session opening
and closing goes through app.core.tasks + app.core.audit, so it is visible in
the Tasks tab AND in the Journal).

"""

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import secrets
import signal
import socket
import struct
import subprocess
import termios
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core import deployment_profile
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.host_capabilities import get_local_capabilities
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task
from app.core.vm_limits import compute_limits

router = APIRouter(prefix="/host", tags=["host"])


@router.get("/limits")
def host_vm_limits(user: dict = Depends(get_current_user)):
    """Per-VM resource limits derived from the real host. Used by the UI to bound
    form fields and by the backend validation. Each limit reports its source
    (detected/configuration/fallback)."""
    return compute_limits()


class ProfileChoice(BaseModel):
    profil: str  # "auto" or a profile name


def _profile_payload():
    active = deployment_profile.get_active()
    limits = compute_limits(force=True)
    d = dict(active["reglages"]["vm_defaults"])
    # Default values of the creation wizard: never beyond the limits actually
    # computed for this host.
    d["vcpu"] = max(limits["vcpu"]["min"], min(d["vcpu"], limits["vcpu"]["max"]))
    d["memory_mb"] = max(limits["memoire_mo"]["min"], min(d["memory_mb"], limits["memoire_mo"]["max"]))
    d["disk_gb"] = max(limits["disque_go"]["min"], min(d["disk_gb"], limits["disque_go"]["max"]))
    from app.core import vm_limits

    return {
        **active,
        "vm_defaults_effectifs": d,
        "profils": deployment_profile.PROFILES,
        "allocation": {**vm_limits.get_policy(), "politiques": vm_limits.POLICIES},
    }


@router.get("/profile")
def host_profile(user: dict = Depends(get_current_user)):
    """Active deployment profile: homelab/standard/advanced, recommended from the
    detected hardware or chosen by an admin. See app/core/deployment_profile.py."""
    return _profile_payload()


@router.put("/profile")
def set_host_profile(payload: ProfileChoice, user: dict = Depends(require_role("admin"))):
    choice = payload.profil.strip().lower()
    if choice != "auto" and choice not in deployment_profile.PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Profil inconnu : {payload.profil} (auto, {', '.join(deployment_profile.PROFILES)})",
        )
    if deployment_profile.env_override():
        raise HTTPException(
            status_code=409,
            detail="The profile is forced by the HYPERLITE_PROFILE environment variable, which takes precedence over this choice.",
        )
    deployment_profile.set_choice(choice)
    log_action(user["username"], "set_host_profile", "local", "succes", f"profil={choice}")
    return _profile_payload()


class AllocationChoice(BaseModel):
    politique: str


@router.put("/allocation")
def set_host_allocation(payload: AllocationChoice, user: dict = Depends(require_role("admin"))):
    """VM resource allocation policy (limits/overcommit/free). See
    app/core/vm_limits.py."""
    from app.core import vm_limits

    choice = payload.politique.strip().lower()
    if choice not in vm_limits.POLICIES:
        raise HTTPException(
            status_code=400, detail=f"Politique inconnue : {payload.politique} ({', '.join(vm_limits.POLICIES)})"
        )
    if vm_limits.env_policy():
        raise HTTPException(
            status_code=409,
            detail="The policy is forced by the HYPERLITE_ALLOCATION environment variable, which takes precedence over this choice.",
        )
    vm_limits.set_policy(choice)
    log_action(user["username"], "set_host_allocation", "local", "succes", f"politique={choice}")
    return _profile_payload()


@router.get("/capabilities")
def host_capabilities(user: dict = Depends(get_current_user)):
    """Capability profile of the LOCAL host. The foundation for dynamic VM limits,
    the "Compatibility and capabilities" page and the cluster compatibility
    diagnostic. See app/core/host_capabilities.py for the detail of each
    sub-profile."""
    try:
        result = get_local_capabilities()
    except Exception as e:
        msg = describe_exception(e)
        log_action(user["username"], "get_host_capabilities", "local", "echec", msg)
        raise HTTPException(status_code=500, detail=f"Capability discovery error: {msg}") from e
    log_action(user["username"], "get_host_capabilities", "local", "succes")
    return result


@router.get("/preflight")
def host_preflight(user: dict = Depends(require_role("admin"))):
    """Preflight check replayed live on the local host: the same checks as at
    installation time (see app/core/preflight.py), with Python dependencies
    probed in the interpreter of the running service itself."""
    import sys

    from app.core import preflight

    try:
        report = preflight.run(python=sys.executable, requirements=str(preflight.APP_DIR / "requirements.txt"))
    except Exception as e:
        msg = describe_exception(e)
        log_action(user["username"], "host_preflight", "local", "echec", msg)
        raise HTTPException(status_code=500, detail=f"Preflight error: {msg}") from e
    log_action(user["username"], "host_preflight", "local", "succes")
    return report


# Same single-use, short-lived ticket pattern as TERMINAL_TICKETS in
# app/routers/vms.py (VM console/terminal): a regular JWT would stay valid for
# its whole lifetime if intercepted, whereas a ticket is consumed (popped) on
# the first WebSocket connection.
HOST_SHELL_TICKETS = {}
HOST_SHELL_TICKET_TTL = 30


def _set_pty_size(fd, cols, rows):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@router.post("/terminal-ticket")
def create_host_terminal_ticket(user: dict = Depends(require_role("admin"))):
    now = time.time()
    for old_ticket, (_old_user, old_expiry) in list(HOST_SHELL_TICKETS.items()):
        if old_expiry < now:
            HOST_SHELL_TICKETS.pop(old_ticket, None)

    ticket = secrets.token_urlsafe(24)
    HOST_SHELL_TICKETS[ticket] = (user["username"], now + HOST_SHELL_TICKET_TTL)
    log_action(user["username"], "create_host_terminal_ticket", socket.gethostname(), "succes")
    return {"ticket": ticket, "expire_dans_s": HOST_SHELL_TICKET_TTL}


@router.websocket("/terminal")
async def host_terminal(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket")
    entry = HOST_SHELL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    username, expiry = entry
    if time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    hostname = socket.gethostname()

    # A shell session can last hours. Unlike instantaneous VM actions, "termine" at
    # task closing only means "session closed cleanly" here (see also finish_task
    # below), not a success/failure of an operation in the usual sense.
    task_id = create_task("host_shell", hostname, node=hostname, username=username)
    log_action(username, "host_shell_open", hostname, "succes")

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 80, 24)
    shell = os.environ.get("SHELL", "/bin/bash")
    try:
        proc = subprocess.Popen(
            [shell, "-l"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            # New session (setsid): the shell gets real terminal control (Ctrl+C, job
            # control, interactive commands such as `top`/`vim`) instead of staying attached
            # to uvicorn's process group.
            preexec_fn=os.setsid,
            env={**os.environ, "TERM": "xterm-256color"},
            close_fds=True,
        )
    finally:
        # The parent no longer needs its end of the pty once the child process is
        # started (only the child keeps it open through stdin/stdout/stderr). Without
        # this close, master_fd would never see an EOF when the shell ends.
        os.close(slave_fd)

    os.set_blocking(master_fd, False)
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()

    def _on_readable():
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data)
        if not data:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_reader(master_fd)

    loop.add_reader(master_fd, _on_readable)

    async def pty_to_ws():
        while True:
            data = await queue.get()
            if not data:
                break
            await websocket.send_text(data.decode(errors="replace"))

    async def ws_to_pty():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        _set_pty_size(master_fd, int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        pass
                else:
                    os.write(master_fd, msg.encode())
        except (WebSocketDisconnect, RuntimeError):
            pass
        except OSError:
            pass

    task1 = asyncio.ensure_future(pty_to_ws())
    task2 = asyncio.ensure_future(ws_to_pty())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    with contextlib.suppress(ValueError, OSError):
        loop.remove_reader(master_fd)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with contextlib.suppress(OSError):
        os.close(master_fd)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    finish_task(task_id, "termine")
    log_action(username, "host_shell_close", hostname, "succes")
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()
