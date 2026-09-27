"""Network tunnel from a user's workstation to one port of a VM (SSH, remote
desktop), for the `hyperlite` client: see app/core/workstation.py.

The client asks for a ticket over the authenticated API, then opens the WebSocket
with it; the server connects to the VM and relays the bytes both ways. The guest
protocol is relayed untouched, so SSH or RDP stay encrypted between the
workstation and the VM, and the user still signs in to the guest with the guest's
own credentials: the tunnel only grants network reachability, to allowed ports,
to users who hold the vm.tunnel privilege on that VM."""

import asyncio
import contextlib
import logging
import time
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Body, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core import workstation
from app.core.audit import log_action, request_ip
from app.core.libvirt_utils import open_conn
from app.core.security import require_vm_privilege
from app.core.vm_meta import get_vm_ssh_user
from app.routers.vms._shared import _get_ip, router

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10
_CHUNK = 65536


def _guest_ip(domain):
    """The DHCP lease first (the usual case), then the ARP table of the host, which
    also covers VMs with a static address on a libvirt network."""
    ip = _get_ip(domain)
    if ip:
        return ip
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP)
    except libvirt.libvirtError:
        logger.debug("ARP address lookup failed", exc_info=True)
        return None
    for iface in ifaces.values():
        for addr in iface.get("addrs", []):
            if addr.get("type") == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                return addr.get("addr")
    return None


@router.post("/{name}/tunnel-ticket")
def create_tunnel_ticket(
    name: str, port: int = Body(..., embed=True), user: dict = Depends(require_vm_privilege("vm.tunnel"))
):
    allowed = workstation.tunnel_ports()
    if port not in allowed:
        log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "echec", "port not allowed")
        detail = (
            "Tunnels are disabled on this server."
            if not allowed
            else f"Port {port} is not allowed. Allowed ports: {', '.join(map(str, allowed))}."
        )
        raise HTTPException(status_code=403, detail=detail)
    if workstation.open_tunnel_count(user["username"]) >= workstation.tunnel_max_per_user():
        log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "echec", "too many open tunnels")
        raise HTTPException(status_code=429, detail="Too many open tunnels for this account: close some first.")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found on this node") from None
        if not domain.isActive():
            log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "echec", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be running.")
        ip = _guest_ip(domain)
    finally:
        conn.close()
    if not ip:
        log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "echec", "IP unknown")
        raise HTTPException(status_code=409, detail="The VM has no known IP address yet (still booting?).")
    ticket = workstation.issue_tunnel_ticket(name, ip, port, user["username"])
    log_action(user["username"], "create_tunnel_ticket", f"{name}:{port}", "succes")
    return {"ticket": ticket, "expire_dans_s": workstation.TUNNEL_TICKET_TTL}


@router.websocket("/{name}/tunnel")
async def vm_tunnel(websocket: WebSocket, name: str):
    entry = workstation.take_tunnel_ticket(websocket.query_params.get("ticket"), name)
    # Accepted before any check so that the client gets a close code and a reason it
    # can show, instead of a bare refused handshake.
    await websocket.accept()
    if entry is None:
        await websocket.close(code=4401, reason="invalid or expired ticket")
        return
    username, port = entry["username"], entry["port"]
    target = f"{name}:{port}"
    # The HTTP middleware that records the client address does not run for WebSockets.
    ip_token = request_ip.set(websocket.client.host if websocket.client else None)
    try:
        if not workstation.tunnel_opened(username):
            log_action(username, "tunnel", target, "echec", "too many open tunnels")
            await websocket.close(code=4429, reason="too many open tunnels")
            return
        try:
            await _relay(websocket, entry, username, target)
        finally:
            workstation.tunnel_closed(username)
    finally:
        request_ip.reset(ip_token)


async def _relay(websocket, entry, username, target):
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(entry["ip"], entry["port"]), CONNECT_TIMEOUT_S)
    except (TimeoutError, OSError) as e:
        log_action(username, "tunnel", target, "echec", f"connection to the VM failed: {type(e).__name__}")
        await websocket.close(code=4502, reason=f"the VM does not answer on port {entry['port']}")
        return
    started = time.monotonic()
    last_activity = [started]
    sent = [0, 0]  # bytes workstation -> VM, VM -> workstation
    idle_timeout = workstation.tunnel_idle_timeout_s()

    async def ws_to_vm():
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, ConnectionError):
            while True:
                data = await websocket.receive_bytes()
                last_activity[0] = time.monotonic()
                sent[0] += len(data)
                writer.write(data)
                await writer.drain()

    async def vm_to_ws():
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, ConnectionError):
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                last_activity[0] = time.monotonic()
                sent[1] += len(data)
                await websocket.send_bytes(data)

    async def idle_watch():
        while time.monotonic() - last_activity[0] < idle_timeout:
            await asyncio.sleep(min(30, idle_timeout))

    log_action(username, "tunnel_open", target, "succes")
    tasks = {asyncio.ensure_future(ws_to_vm()), asyncio.ensure_future(vm_to_ws()), asyncio.ensure_future(idle_watch())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            logger.debug("Tunnel relay ended with an error", exc_info=task.exception())
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()
    duration = round(time.monotonic() - started)
    idle = time.monotonic() - last_activity[0] >= idle_timeout
    log_action(
        username,
        "tunnel_close",
        target,
        "succes",
        f"{duration} s, {sent[0]} bytes sent, {sent[1]} bytes received" + (", closed after inactivity" if idle else ""),
    )


# Libvirt network forward modes that put the guest on a network of the site (its
# address is then reachable from the workstations, like any machine of that LAN or VLAN).
_DIRECT_FORWARD_MODES = {"bridge"}


def _directly_reachable(conn, root):
    for iface in root.findall("./devices/interface"):
        kind = iface.get("type")
        if kind in ("bridge", "direct"):  # host bridge or macvtap: on the physical network
            return True
        if kind == "network":
            source = iface.find("source")
            net_name = source.get("network") if source is not None else None
            if not net_name:
                continue
            try:
                net = ET.fromstring(conn.networkLookupByName(net_name).XMLDesc(0))
            except libvirt.libvirtError:
                continue
            forward = net.find("forward")
            if forward is not None and forward.get("mode") in _DIRECT_FORWARD_MODES:
                return True
    return False


@router.get("/{name}/access")
def vm_access(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    """How a workstation can reach this VM: directly (a bridged network of the site,
    like any machine of the LAN) or through a tunnel of the hyperlite client."""
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found on this node") from None
        root = ET.fromstring(domain.XMLDesc(0))
        active = domain.isActive()
        ip = _guest_ip(domain) if active else None
        direct = _directly_reachable(conn, root)
    finally:
        conn.close()
    return {
        "ip": ip,
        "direct": bool(direct and ip),
        "ssh_user": get_vm_ssh_user(name),
        "tunnel_ports": workstation.tunnel_ports(),
    }
