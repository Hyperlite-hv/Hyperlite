"""Remote desktop in the browser (docs/design/console-2.md, section 8; the daemon's side: app/daemon/ops/rdp.py).

A ticket request carries the account and password the person typed: they stay in this process's memory with the
single-use ticket (30 seconds), go to hyperlite-daemon once when the WebSocket opens, and are never written by the API.
The WebSocket then relays the session's VNC display to noVNC, as a VM's console does, and ends the session when it
closes."""

import asyncio
import logging
import secrets
import socket
import time

from fastapi import Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core import peer_auth, privileged
from app.core.audit import log_action
from app.core.security import require_vm_privilege
from app.daemon.registry import DaemonError
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


def _reason(text):
    """A WebSocket close reason: at most 123 bytes of UTF-8."""
    return text.encode()[:120].decode(errors="ignore")


TICKET_TTL_S = 30
_TICKETS = {}  # ticket -> (vm, node, username, request, expiry)


class RdpTicketIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=256)
    domain: str = Field("", max_length=128)
    width: int = Field(1280, ge=320, le=7680)
    height: int = Field(800, ge=200, le=4320)
    port: int = Field(3389, ge=1, le=65535)


@router.get("/{name}/rdp")
async def rdp_available(name: str, user: dict = Depends(require_vm_privilege("vm.console"))):
    """Whether this node can open a remote desktop (its tools installed)."""
    from app.core import rdp_box
    from app.daemon.ops.rdp import missing_tools

    missing = missing_tools()
    return {"available": not missing, "missing": missing, "packages": rdp_box.PACKAGES}


@router.post("/{name}/rdp-ticket")
async def create_rdp_ticket(
    name: str, payload: RdpTicketIn, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))
):
    now = time.time()
    for old, entry in list(_TICKETS.items()):
        if entry[4] < now:
            _TICKETS.pop(old, None)
    ticket = secrets.token_urlsafe(24)
    _TICKETS[ticket] = (
        name,
        None if not node or node == "local" else node,
        user["username"],
        payload,
        now + TICKET_TTL_S,
    )
    peer_auth.keep_with_ticket(ticket, TICKET_TTL_S)
    return {"ticket": ticket, "expires_in_s": TICKET_TTL_S}


@router.websocket("/{name}/rdp")
async def rdp_session(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = _TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return
    vm, node, username, req, expiry = entry
    peer_auth.take_ticket_credential(ticket)
    if vm != name or time.time() > expiry:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    sid = secrets.token_hex(8)
    try:
        opened = await run_in_threadpool(
            privileged.run, "rdp.open", user=username, vm=vm, node=node, sid=sid, width=req.width, height=req.height,
            port=req.port, username=req.username, password=req.password, domain=req.domain, timeout=40,
        )  # fmt: skip
        _result, fds = await run_in_threadpool(privileged.run_fds, "rdp.attach", user=username, sid=sid, vm=vm)
    except DaemonError as e:
        log_action(username, "rdp_open", vm, "failed", e.detail)
        await websocket.close(code=4500, reason=_reason(e.detail))
        return
    del req, entry  # the password is not kept past the daemon's answer
    log_action(username, "rdp_open", vm, "success", f"to {opened.get('address')}")
    sock = socket.socket(fileno=fds[0])
    reader, writer = await asyncio.open_connection(sock=sock)

    async def ws_to_display():
        try:
            while True:
                writer.write(await websocket.receive_bytes())
                await writer.drain()
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Remote desktop: the browser left", exc_info=True)
        except Exception:
            logger.debug("Remote desktop: browser side ended", exc_info=True)

    async def display_to_ws():
        try:
            while data := await reader.read(65536):
                await websocket.send_bytes(data)
        except Exception:
            logger.debug("Remote desktop: display side ended", exc_info=True)

    tasks = {asyncio.ensure_future(ws_to_display()), asyncio.ensure_future(display_to_ws())}
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    writer.close()
    why = ""
    try:
        why = (await run_in_threadpool(privileged.run, "rdp.close", user=username, sid=sid, vm=vm)).get("why") or ""
    except DaemonError as e:
        logger.warning("Remote desktop %s not stopped: %s", sid, e.detail)
    log_action(username, "rdp_close", vm, "success", why[:200] or "closed")
    try:
        await websocket.close(code=4500 if why else 1000, reason=_reason(why))
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Remote desktop: already closed", exc_info=True)
