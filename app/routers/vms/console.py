import asyncio
import json
import logging
import os
import secrets
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import asyncssh
import libvirt
from fastapi import Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.core import cluster_view, pty_session, spice, spice_proxy
from app.core.audit import log_action
from app.core.cluster import get_cluster_private_key_path, get_node, libvirt_uri_from_here
from app.core.libvirt_utils import (
    LIBVIRT_URI,
    ensure_vnc_graphics,
    open_conn,
)
from app.core.security import require_role, require_vm_privilege
from app.core.vm_builder import (
    get_automation_private_key_path,
)
from app.core.vm_meta import (
    get_vm_ssh_user,
)
from app.routers.vms._shared import _get_ip, router
from app.routers.vms.runtime import CONSOLE_TICKET_TTL, CONSOLE_TICKETS

logger = logging.getLogger(__name__)

# The trust store the cluster's ssh connections already use (see cluster.node_ssh_options): a node's host key is
# verified here too, never accepted blindly.
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"


def _remote(node):
    """The registered node a VM lives on, or None for the local host."""
    return None if not node or node == "local" else node


async def _connect_to_node(node_name):
    """SSH connection to a registered node with the cluster key. A VM of a remote node has its VNC server on THAT
    node's loopback, and its IP address on THAT node's networks: both are reached through this connection."""
    node = get_node(node_name)
    if node is None:
        raise OSError(f"Node '{node_name}' not found")
    return await asyncssh.connect(
        node["hostname"],
        port=int(node["ssh_port"]),
        username=node["ssh_user"],
        client_keys=[str(get_cluster_private_key_path())],
        known_hosts=str(KNOWN_HOSTS),
        connect_timeout=10,
    )


@router.post("/{name}/console-ticket")
@cluster_view.stays_here
def create_console_ticket(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))):
    """node: the registered node the VM runs on (the local host by default). Without it, a VM of a remote node was
    looked up on the local host: a 404, or the console of a local VM that happens to have the same name."""
    node = _remote(node)
    conn = open_conn(node)
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
            ensure_vnc_graphics(conn, domain)
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
        CONSOLE_TICKETS[ticket] = (name, int(port), now + CONSOLE_TICKET_TTL, node)
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

    vm_name, port, expiry, node = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    node_ssh = None
    try:
        if node:
            node_ssh = await _connect_to_node(node)
            reader, writer = await node_ssh.open_connection("127.0.0.1", port)
        else:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except (asyncssh.Error, OSError):
        logger.warning(
            "Console of %s: cannot reach its VNC server%s", name, f" on {node}" if node else "", exc_info=True
        )
        if node_ssh is not None:
            node_ssh.close()
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
    if node_ssh is not None:
        node_ssh.close()
    try:
        await websocket.close()
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
    conn = open_conn(node)
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
            log_action(user["username"], "create_terminal_ticket", name, "failed", "IP inconnue")
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
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL, node)
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

    vm_name, ip, ssh_user, expiry, node = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    private_key = get_automation_private_key_path()
    node_ssh = None
    try:
        if node:
            node_ssh = await _connect_to_node(node)
        ssh_conn = await asyncssh.connect(
            ip,
            username=ssh_user,
            client_keys=[str(private_key)],
            known_hosts=None,
            connect_timeout=10,
            tunnel=node_ssh,
        )
    except (asyncssh.Error, OSError) as e:
        if node_ssh is not None:
            node_ssh.close()
        via = f" through node {node}" if node else ""
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] SSH connection to {ip}{via} failed: {e}\x1b[0m\r\n")
        await websocket.close(code=1011)
        return

    try:
        process = await ssh_conn.create_process(term_type="xterm-256color", term_size=(80, 24))
    except asyncssh.Error as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] Failed to open the shell: {e}\x1b[0m\r\n")
        ssh_conn.close()
        await websocket.close(code=1011)
        return

    async def ws_to_ssh():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        process.change_terminal_size(int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
                else:
                    process.stdin.write(msg)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        except Exception:
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        finally:
            try:
                process.stdin.write_eof()
            except Exception:
                logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)

    async def ssh_to_ws():
        try:
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    break
                await websocket.send_text(data)
        except Exception:
            logger.debug("Ignored exception in ssh_to_ws()", exc_info=True)

    task1 = asyncio.ensure_future(ws_to_ssh())
    task2 = asyncio.ensure_future(ssh_to_ws())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        process.close()
    except Exception:
        logger.debug("Ignored exception in vm_terminal()", exc_info=True)
    ssh_conn.close()
    if node_ssh is not None:
        node_ssh.close()
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


def _has_serial(xml):
    devices_el = ET.fromstring(xml).find("devices")
    return devices_el is not None and (devices_el.find("serial") is not None or devices_el.find("console") is not None)


def add_serial_port(conn, domain):
    """Give a VM without a serial port one, in its configuration: a serial port cannot be plugged into a running
    VM, so a running one gets it at its next start."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    devices_el = root.find("devices")
    serial = ET.SubElement(devices_el, "serial", type="pty")
    ET.SubElement(serial, "target", port="0")
    console = ET.SubElement(devices_el, "console", type="pty")
    ET.SubElement(console, "target", type="serial", port="0")
    conn.defineXML(ET.tostring(root, encoding="unicode"))


@router.post("/{name}/serial-ticket")
@cluster_view.stays_here
def create_serial_ticket(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.console"))):
    """node: as for the console ticket. A VM with no serial port gets one, effective at its next start."""
    node = _remote(node)
    conn = open_conn(node)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_serial_ticket", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not _has_serial(domain.XMLDesc(0)):
            if _has_serial(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)):
                detail = "The serial port added to this VM takes effect at its next start: shut it down, then start it."
            else:
                add_serial_port(conn, domain)
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
        # The VM by the UUID libvirt gave it: the command line never carries what the request named.
        SERIAL_TICKETS[ticket] = (name, now + SERIAL_TICKET_TTL, node, domain.UUIDString())
        log_action(user["username"], "create_serial_ticket", name, "success")
        return {"ticket": ticket, "expires_in_s": SERIAL_TICKET_TTL}
    finally:
        conn.close()


def serial_command(uuid, node):
    """`virsh console` on the VM's node: through the same libvirt address as every call to that node."""
    uri = libvirt_uri_from_here(get_node(node)) if node else LIBVIRT_URI
    return ["virsh", "--quiet", "--connect", uri, "console", "--domain", uuid]


@router.websocket("/{name}/serial")
async def vm_serial(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = SERIAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, expiry, node, uuid = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    # virsh prints its own messages in the user's language: C.UTF-8 keeps them English and the guest's UTF-8 intact.
    await pty_session.run(
        websocket, serial_command(uuid, node), {**os.environ, "TERM": "xterm-256color", "LC_ALL": "C.UTF-8"}
    )
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
    conn = open_conn(node)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "add_spice_display", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if spice.spice_graphics(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)) is not None:
            log_action(user["username"], "add_spice_display", name, "failed", "already has one")
            raise HTTPException(status_code=409, detail="This VM already has a SPICE display")
        try:
            spice.node_tls(node)
        except spice.SpiceUnavailable as e:
            log_action(user["username"], "add_spice_display", name, "failed", str(e))
            raise HTTPException(
                status_code=409, detail=f"{e}: the package sets it up (scripts/ensure-spice-tls.sh)"
            ) from None
        spice.add_display(conn, domain)
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
    conn = open_conn(node)
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
        try:
            ca_pem, subject = spice.node_tls(node)
        except spice.SpiceUnavailable as e:
            log_action(user["username"], "create_spice_ticket", name, "failed", str(e))
            raise HTTPException(status_code=409, detail=str(e)) from None
        password = spice.set_ticket_password(domain)
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
