import asyncio
import json
import logging
import secrets
import time
import xml.etree.ElementTree as ET

import asyncssh
import libvirt
from fastapi import Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core.audit import log_action
from app.core.libvirt_utils import (
    ensure_vnc_graphics,
    open_conn,
)
from app.core.security import require_vm_privilege
from app.core.vm_builder import (
    get_automation_private_key_path,
)
from app.core.vm_meta import (
    get_vm_ssh_user,
)
from app.routers.vms._shared import _get_ip, router
from app.routers.vms.runtime import CONSOLE_TICKET_TTL, CONSOLE_TICKETS

logger = logging.getLogger(__name__)


@router.post("/{name}/console-ticket")
def create_console_ticket(name: str, user: dict = Depends(require_vm_privilege("vm.console"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_console_ticket", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        graphics = devices_el.find("graphics[@type='vnc']") if devices_el is not None else None

        if graphics is None:
            if domain.isActive():
                log_action(user["username"], "create_console_ticket", name, "echec", "no VNC (VM running)")
                raise HTTPException(
                    status_code=409,
                    detail="This VM was created before the console was added. Stop it, then start it once again to enable the console.",
                )
            ensure_vnc_graphics(conn, domain)
            log_action(user["username"], "create_console_ticket", name, "echec", "VNC added, VM stopped")
            raise HTTPException(status_code=409, detail="Console enabled on this VM: start it, then try again.")

        if not domain.isActive():
            log_action(user["username"], "create_console_ticket", name, "echec", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a console")

        port = graphics.get("port")
        if not port or port == "-1":
            log_action(user["username"], "create_console_ticket", name, "echec", "VNC port unavailable")
            raise HTTPException(status_code=500, detail="VNC port not available yet")

        now = time.time()
        for old_ticket, (_old_vm, _old_port, old_expiry) in list(CONSOLE_TICKETS.items()):
            if old_expiry < now:
                CONSOLE_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        CONSOLE_TICKETS[ticket] = (name, int(port), now + CONSOLE_TICKET_TTL)
        log_action(user["username"], "create_console_ticket", name, "succes")
        return {"ticket": ticket, "expire_dans_s": CONSOLE_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/console")
async def vm_console(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = CONSOLE_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, port, expiry = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
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
    try:
        await websocket.close()
    except RuntimeError:
        logger.debug("Ignored exception in vm_console()", exc_info=True)


# --- Web SSH terminal (xterm.js + a remote shell through the automation key) ---
# Restricted to the admin role: the automation key connects to the VM's cloud-init
# user, which has full passwordless sudo, so opening this terminal is equivalent to
# root access.

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30


@router.post("/{name}/terminal-ticket")
def create_terminal_ticket(name: str, user: dict = Depends(require_vm_privilege("vm.console"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not domain.isActive():
            log_action(user["username"], "create_terminal_ticket", name, "echec", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a terminal")

        ip = _get_ip(domain)
        if not ip:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "IP inconnue")
            raise HTTPException(status_code=409, detail="VM IP address not known yet (no DHCP lease yet?)")

        ssh_user = get_vm_ssh_user(name)
        if not ssh_user:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "unknown SSH user")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No known SSH user for '{name}' (VM created before this feature). "
                    "Deploy the automation key with a manual ssh-copy-id, then try again."
                ),
            )

        now = time.time()
        for old_ticket, (_old_vm, _old_ip, _old_user, old_expiry) in list(TERMINAL_TICKETS.items()):
            if old_expiry < now:
                TERMINAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL)
        log_action(user["username"], "create_terminal_ticket", name, "succes")
        return {"ticket": ticket, "utilisateur": ssh_user, "expire_dans_s": TERMINAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/terminal")
async def vm_terminal(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = TERMINAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, ip, ssh_user, expiry = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    private_key = get_automation_private_key_path()
    try:
        ssh_conn = await asyncssh.connect(
            ip,
            username=ssh_user,
            client_keys=[str(private_key)],
            known_hosts=None,
            connect_timeout=10,
        )
    except (asyncssh.Error, OSError) as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] SSH connection to {ip} failed: {e}\x1b[0m\r\n")
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
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in vm_terminal()", exc_info=True)
