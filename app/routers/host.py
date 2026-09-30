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

import contextlib
import os
import secrets
import socket
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core import firmware, passthrough, pty_session
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.host_capabilities import get_local_capabilities
from app.core.libvirt_utils import open_conn
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task
from app.core.vm_limits import compute_limits

router = APIRouter(prefix="/host", tags=["host"])


@router.get("/limits")
def host_vm_limits(user: dict = Depends(get_current_user)):
    """Per-VM resource bounds (see app/core/vm_limits.py): technical ones, or caps an administrator set in the
    environment. Used by the UI to bound form fields and by the backend validation."""
    return compute_limits()


@router.get("/firmware")
def host_firmware(user: dict = Depends(get_current_user)):
    """Which VM firmwares this host can build: {"uefi", "uefi_secure", "raison"} (see app/core/firmware.py). BIOS
    is always possible. The creation form disables what is missing and shows the reason."""
    conn = open_conn()
    try:
        return firmware.host_support(conn)
    finally:
        conn.close()


@router.get("/devices")
def host_devices(user: dict = Depends(require_role("admin"))):
    """PCI and USB devices of this host that could be given to a VM, with the IOMMU state, which VM has each one,
    and why the host needs the ones it cannot give (see app/core/passthrough.py)."""
    conn = open_conn()
    try:
        return passthrough.list_devices(conn)
    finally:
        conn.close()


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

    shell = os.environ.get("SHELL", "/bin/bash")
    await pty_session.run(websocket, [shell, "-l"], {**os.environ, "TERM": "xterm-256color"})

    finish_task(task_id, "termine")
    log_action(username, "host_shell_close", hostname, "succes")
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()
