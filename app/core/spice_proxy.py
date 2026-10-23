"""The SPICE proxy (docs/design/spice-console.md), as Proxmox VE's spiceproxy: virt-viewer sends
`CONNECT <ticket>:<port>` here (the .vv names this proxy), and the proxy relays the connection to the VM's SPICE TLS
port on 127.0.0.1 of its node, through the cluster's SSH connection for another node, as the noVNC console does.

It cannot be Hyperlite's HTTPS port: uvicorn answers CONNECT with 400 (httptools), or hands it to the application with
no way to take over the socket (h11). So it is a listener of its own, in the same process, started with the API.

A ticket admits connections to its own VM and port while it is valid: virt-viewer opens one connection per SPICE
channel. Anything else is answered 403 and closed. The proxy only ever sees TLS: the client checks QEMU's certificate
against the node's CA, and QEMU checks the ticket's password.
"""

import asyncio
import logging
import secrets
import time

from app.core import spice

logger = logging.getLogger(__name__)

# ticket -> (VM name, node or None, TLS port, expiry)
TICKETS = {}
HEADER_LIMIT = 4096
HEADER_TIMEOUT_S = 10
_server = None
_clients = set()  # the writers of the connections open now, closed at stop()


def issue(vm_name, node, port, ttl_s=spice.TICKET_TTL_S):
    """A ticket for the VM's SPICE TLS port, valid ttl_s seconds. Expired tickets are dropped on the way."""
    now = time.time()
    for old, entry in list(TICKETS.items()):
        if entry[3] < now:
            TICKETS.pop(old, None)
    # Hexadecimal: it travels as the host name of the CONNECT request.
    ticket = secrets.token_hex(20)
    TICKETS[ticket] = (vm_name, node, int(port), now + ttl_s)
    return ticket


def admit(request_line):
    """(node, port) for a valid `CONNECT <ticket>:<port> HTTP/1.x` line, else None."""
    parts = request_line.split()
    if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1."):
        return None
    ticket, _, port = parts[1].rpartition(":")
    entry = TICKETS.get(ticket)
    if entry is None or not port.isdigit():
        return None
    _vm, node, ticket_port, expiry = entry
    if time.time() > expiry or int(port) != ticket_port:
        return None
    return node, ticket_port


async def _open(node, port):
    if node is None:
        return await asyncio.open_connection("127.0.0.1", port), None
    from app.routers.vms.console import _connect_to_node

    ssh = await _connect_to_node(node)
    try:
        return await ssh.open_connection("127.0.0.1", port), ssh
    except Exception:
        ssh.close()
        raise


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        logger.debug("SPICE relay closed by the other side", exc_info=True)
    finally:
        writer.close()


async def _refuse(writer, status):
    writer.write(f"HTTP/1.0 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    try:
        await writer.drain()
    except (ConnectionError, OSError):
        logger.debug("SPICE proxy: the client left before the refusal", exc_info=True)
    writer.close()


async def _handle(client_reader, client_writer):
    _clients.add(client_writer)
    try:
        await _serve(client_reader, client_writer)
    finally:
        _clients.discard(client_writer)


async def _serve(client_reader, client_writer):
    peer = client_writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_S)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_writer.close()
        return
    request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    target = admit(request_line)
    if target is None:
        # The ticket is a secret: the refusal is logged without the request line.
        logger.warning("SPICE proxy: refused a connection from %s (no valid ticket)", peer[0] if peer else "?")
        await _refuse(client_writer, "403 Forbidden")
        return
    node, port = target
    try:
        (vm_reader, vm_writer), ssh = await _open(node, port)
    except Exception:
        logger.warning("SPICE proxy: cannot reach port %s%s", port, f" on {node}" if node else "", exc_info=True)
        await _refuse(client_writer, "502 Bad Gateway")
        return
    client_writer.write(b"HTTP/1.0 200 Connection established\r\n\r\n")
    try:
        await client_writer.drain()
        await asyncio.gather(_pipe(client_reader, vm_writer), _pipe(vm_reader, client_writer))
    finally:
        if ssh is not None:
            ssh.close()


async def start(host=None, port=None):
    """Listen for virt-viewer, on every address of the node (IPv6 included) as the API does; port 0 in
    HYPERLITE_SPICE_PROXY_PORT: not at all (an explicit port=0 is any free port). A port already taken leaves the API
    running without SPICE consoles, said in the log."""
    global _server
    if port is None:
        port = spice.PROXY_PORT
        if not port:
            return None
    try:
        _server = await asyncio.start_server(_handle, host, port, limit=HEADER_LIMIT)
    except OSError as e:
        logger.error("SPICE proxy: cannot listen on port %s (%s): SPICE consoles are unavailable", port, e)
        return None
    logger.info("SPICE proxy listening on port %s", port)
    return _server


async def stop():
    """Stop listening and close the open sessions: wait_closed() waits for every connection, and a SPICE session left
    open would otherwise hold the service's stop."""
    global _server
    if _server is not None:
        _server.close()
        # The sessions this proxy keeps itself: the service runs on uvloop, whose server has no close_clients().
        for writer in list(_clients):
            writer.close()
        try:
            await asyncio.wait_for(_server.wait_closed(), 5)
        except TimeoutError:
            logger.warning("SPICE proxy: sessions still closing at the service's stop")
        _server = None
