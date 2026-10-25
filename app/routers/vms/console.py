"""A VM's consoles: the graphical one (noVNC), the SSH terminal, the serial console and the SPICE display. The routes
issue the single-use tickets after their permission checks, read the VM through a read-only connection and bridge
the session to the browser; hyperlite-daemon opens each session (app/daemon/ops/console.py), with the cluster's key
for a VM of another node, and changes a VM's definition when a console must be added."""

import asyncio
import contextlib
import logging
import secrets
import socket
import time
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core import cluster_view, privileged, pty_session, spice, spice_proxy
from app.core.audit import log_action
from app.core.libvirt_utils import has_serial, open_conn
from app.core.security import require_role, require_vm_privilege
from app.core.vm_meta import (
    get_vm_ssh_user,
)
from app.daemon.registry import DaemonError
from app.routers.vms._shared import _get_ip, router
from app.routers.vms.runtime import CONSOLE_TICKET_TTL, CONSOLE_TICKETS

logger = logging.getLogger(__name__)
# ssh's own exit status when it could not connect or sign in: said in the terminal, after ssh's own words.
SSH_FAILED = 255


def _remote(node):
    """The registered node a VM lives on, or None for the local host."""
    return None if not node or node == "local" else node


async def open_display(vm, node, protocol, username=None):
    """A VM's VNC or SPICE display, connected by hyperlite-daemon on its node: (reader, writer, session). Raises
    DaemonError."""
    result, fds = await run_in_threadpool(
        privileged.run_fds, "console.display", user=username, vm=vm, node=node, protocol=protocol
    )
    if not fds:
        await pty_session.end(result.get("session"), username)
        raise DaemonError("failed", "The VM's display could not be reached")
    sock = socket.socket(fileno=fds[0])
    try:
        reader, writer = await asyncio.open_connection(sock=sock)
    except OSError:
        sock.close()
        await pty_session.end(result.get("session"), username)
        raise
    return reader, writer, result.get("session")


@router.post("/{name}/console-ticket")
@cluster_view.stays_here
def create_console_ticket(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))):
    """node: the registered node the VM runs on (the local host by default). Without it, a VM of a remote node was
    looked up on the local host: a 404, or the console of a local VM that happens to have the same name."""
    node = _remote(node)
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_console_ticket", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        graphics = devices_el.find("graphics[@type='vnc']") if devices_el is not None else None

        if graphics is None:
            if domain.isActive():
                log_action(user["username"], "create_console_ticket", name, "failed", "no VNC (VM running)")
                raise HTTPException(
                    status_code=409,
                    detail="This VM was created before the console was added. Stop it, then start it once again to enable the console.",
                )
            privileged.ask(
                "vm.add_console", user, "create_console_ticket", name, refused=409, vm=name, node=node, kind="vnc"
            )
            log_action(user["username"], "create_console_ticket", name, "failed", "VNC added, VM stopped")
            raise HTTPException(status_code=409, detail="Console enabled on this VM: start it, then try again.")

        if not domain.isActive():
            log_action(user["username"], "create_console_ticket", name, "failed", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a console")

        port = graphics.get("port")
        if not port or port == "-1":
            log_action(user["username"], "create_console_ticket", name, "failed", "VNC port unavailable")
            raise HTTPException(status_code=500, detail="VNC port not available yet")

        now = time.time()
        for old_ticket, entry in list(CONSOLE_TICKETS.items()):
            if entry[2] < now:
                CONSOLE_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        CONSOLE_TICKETS[ticket] = (name, int(port), now + CONSOLE_TICKET_TTL, node, user["username"])
        log_action(user["username"], "create_console_ticket", name, "success")
        return {"ticket": ticket, "expires_in_s": CONSOLE_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/console")
async def vm_console(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = CONSOLE_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, _port, expiry, node, username = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    try:
        reader, writer, session = await open_display(name, node, "vnc", username)
    except (DaemonError, OSError) as e:
        logger.warning("Console of %s: cannot reach its VNC server%s: %s", name, f" on {node}" if node else "", e)
        await websocket.close(code=1011)
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Ignored exception in ws_to_tcp()", exc_info=True)
        except Exception:
            logger.debug("Ignored exception in ws_to_tcp()", exc_info=True)
        finally:
            writer.close()

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            logger.debug("Ignored exception in tcp_to_ws()", exc_info=True)

    task1 = asyncio.ensure_future(ws_to_tcp())
    task2 = asyncio.ensure_future(tcp_to_ws())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    writer.close()
    # ssh's own failure (an unknown host key, a node that does not answer): an error for noVNC, as before.
    failed = await pty_session.end(session, username) == SSH_FAILED
    try:
        await websocket.close(code=1011 if failed else 1000)
    except (RuntimeError, WebSocketDisconnect):
        # The browser may have closed the console first: there is nothing left to close.
        logger.debug("Ignored exception in vm_console()", exc_info=True)


# --- Web SSH terminal (xterm.js + a remote shell through the automation key) ---
# Restricted to the admin role: the automation key connects to the VM's cloud-init
# user, which has full passwordless sudo, so opening this terminal is equivalent to
# root access.

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30


@router.post("/{name}/terminal-ticket")
@cluster_view.stays_here
def create_terminal_ticket(name: str, node: str | None = None, user: dict = Depends(require_role("admin"))):
    """node: as for the console ticket. A VM of a remote node is reached through that node (its address is on the
    node's networks, not necessarily routable from here)."""
    node = _remote(node)
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_terminal_ticket", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not domain.isActive():
            log_action(user["username"], "create_terminal_ticket", name, "failed", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a terminal")

        ip = _get_ip(domain)
        if not ip:
            log_action(user["username"], "create_terminal_ticket", name, "failed", "unknown IP address")
            raise HTTPException(status_code=409, detail="VM IP address not known yet (no DHCP lease yet?)")

        ssh_user = get_vm_ssh_user(name)
        if not ssh_user:
            log_action(user["username"], "create_terminal_ticket", name, "failed", "unknown SSH user")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No known SSH user for '{name}' (VM created before this feature). "
                    "Deploy the automation key with a manual ssh-copy-id, then try again."
                ),
            )

        now = time.time()
        for old_ticket, entry in list(TERMINAL_TICKETS.items()):
            if entry[3] < now:
                TERMINAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL, node, user["username"])
        log_action(user["username"], "create_terminal_ticket", name, "success")
        return {"ticket": ticket, "user": ssh_user, "expires_in_s": TERMINAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/terminal")
async def vm_terminal(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = TERMINAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, _ip, _ssh_user, expiry, node, username = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    via = f" through node {node}" if node else ""
    try:
        # hyperlite-daemon runs ssh with the automation key, which only root reads, to the address it reads itself.
        session = await pty_session.open_session("console.terminal", username, vm=name, node=node)
    except DaemonError as e:
        await pty_session.say(websocket, f"SSH connection{via} failed: {e.detail}")
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)
        return
    if await session.serve(websocket) == SSH_FAILED:
        await pty_session.say(websocket, f"SSH connection to {session.result['address']}{via} failed")
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in vm_terminal()", exc_info=True)


# --- Serial console (xterm.js + `virsh console`) ---
# What the guest prints on its first serial port, and the login prompt its system offers there: the way in when the
# guest's network or display is broken, as Proxmox VE's xterm.js console. It reaches the same login prompt as the
# graphical console, so it takes the same privilege, not the administrator role the SSH terminal needs.

SERIAL_TICKETS = {}
SERIAL_TICKET_TTL = 30


@router.post("/{name}/serial-ticket")
@cluster_view.stays_here
def create_serial_ticket(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))):
    """node: as for the console ticket. A VM with no serial port gets one, effective at its next start."""
    node = _remote(node)
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_serial_ticket", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not has_serial(domain.XMLDesc(0)):
            added = privileged.ask(
                "vm.add_console", user, "create_serial_ticket", name, refused=409, vm=name, node=node, kind="serial"
            )["added"]
            if not added:
                detail = "The serial port added to this VM takes effect at its next start: shut it down, then start it."
            else:
                detail = (
                    "This VM had no serial port: one is added, effective at its next start. Shut it down, then start it."
                    if domain.isActive()
                    else "This VM had no serial port: one is added. Start the VM, then open the serial console."
                )
            log_action(user["username"], "create_serial_ticket", name, "failed", "no serial port")
            raise HTTPException(status_code=409, detail=detail)

        if not domain.isActive():
            log_action(user["username"], "create_serial_ticket", name, "failed", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open its serial console")

        now = time.time()
        for old_ticket, entry in list(SERIAL_TICKETS.items()):
            if entry[1] < now:
                SERIAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        SERIAL_TICKETS[ticket] = (name, now + SERIAL_TICKET_TTL, node, user["username"])
        log_action(user["username"], "create_serial_ticket", name, "success")
        return {"ticket": ticket, "expires_in_s": SERIAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/serial")
async def vm_serial(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = SERIAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, expiry, node, username = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        # `virsh console` by hyperlite-daemon, on the VM's node (console.serial).
        session = await pty_session.open_session("console.serial", username, vm=name, node=node)
    except DaemonError as e:
        await pty_session.say(websocket, e.detail)
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)
        return
    await session.serve(websocket)
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in vm_serial()", exc_info=True)


# --- SPICE console (docs/design/spice-console.md): a .vv file for virt-viewer, through Hyperlite's SPICE proxy ---


@router.post("/{name}/spice-display")
@cluster_view.stays_here
def add_spice_display(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    """Give the VM a SPICE display next to its VNC one, effective at its next start. Refused on a node not set up for
    SPICE in TLS: the VM would no longer start there."""
    node = _remote(node)
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "add_spice_display", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if spice.spice_graphics(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)) is not None:
            log_action(user["username"], "add_spice_display", name, "failed", "already has one")
            raise HTTPException(status_code=409, detail="This VM already has a SPICE display")
        # Refused on a node not set up for SPICE in TLS, with the reason and how the package sets it up.
        privileged.ask("vm.add_console", user, "add_spice_display", name, refused=409, vm=name, node=node, kind="spice")
        running = domain.isActive()
        log_action(user["username"], "add_spice_display", name, "success")
        return {"spice": True, "effective": "next_start" if running else "now"}
    finally:
        conn.close()


@router.post("/{name}/spice-ticket")
@cluster_view.stays_here
def create_spice_ticket(
    name: str, request: Request, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))
):
    """The .vv file of a running VM's SPICE display: a password valid a few seconds, and a ticket for the proxy at the
    address the browser used. virt-viewer deletes the file once read."""
    node = _remote(node)
    proxy = spice.proxy_host(request.headers.get("host"))
    if proxy is None:
        raise HTTPException(status_code=400, detail="The address this request was sent to is not a host name or an IP")
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_spice_ticket", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "create_spice_ticket", name, "failed", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open its SPICE console")
        port = spice.tls_port(domain.XMLDesc(0))
        if port is None:
            stored = spice.spice_graphics(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)) is not None
            log_action(user["username"], "create_spice_ticket", name, "failed", "no SPICE display")
            raise HTTPException(
                status_code=409,
                detail="The SPICE display added to this VM takes effect at its next start: shut it down, then start it."
                if stored
                else "This VM has no SPICE display: add one (it takes effect at the VM's next start).",
            )
        tls = privileged.ask("console.spice_tls", user, "create_spice_ticket", name, refused=409, node=node)
        password = privileged.ask(
            "vm.spice_password", user, "create_spice_ticket", name, refused=409, vm=name, node=node
        )["password"]
        ca_pem, subject = tls["ca"], tls["subject"]
        ticket = spice_proxy.issue(name, node, port)
        log_action(user["username"], "create_spice_ticket", name, "success")
        body = spice.vv_file(
            title=name, ticket=ticket, port=port, password=password, proxy=proxy, ca_pem=ca_pem, subject=subject
        )
        return Response(
            body,
            media_type="application/x-virt-viewer",
            headers={"Content-Disposition": f'attachment; filename="{name}.vv"', "Cache-Control": "no-store"},
        )
    finally:
        conn.close()
