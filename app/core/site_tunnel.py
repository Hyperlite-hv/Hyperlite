"""The tunnel of a live migration between clusters (docs/design/multi-cluster.md, section 9.2, as Proxmox's mtunnel):
one WebSocket over the pinned HTTPS connection from the source's node to the target's node, with the manage token,
carrying the three channels of app/core/site_live.py. Each connection the source's daemon accepts on one of its
sockets (its libvirt, its QEMU) becomes a channel; the target opens its end through its own daemon
(transfer.live_connect), the `libvirt` channel through the relay (app/core/site_relay.py).

Frames, binary WebSocket messages: ">BI" op, channel, then the payload. OPEN's payload is the channel's kind (one
byte, an index of site_live.KINDS); DATA carries bytes; EOF says one side will write no more; CLOSE ends the channel."""

import asyncio
import contextlib
import logging
import os
import socket
import struct
import threading

logger = logging.getLogger(__name__)

FRAME = struct.Struct(">BI")
OPEN, DATA, EOF, CLOSE = 1, 2, 3, 4
KINDS = ("libvirt", "memory", "disks")
CHUNK = 256 * 1024
MAX_CHANNELS = 64


def frame(op, channel, payload=b""):
    return FRAME.pack(op, channel) + payload


def parse(message):
    if not isinstance(message, (bytes, bytearray)) or len(message) < FRAME.size:
        raise ValueError("Not a tunnel frame")
    op, channel = FRAME.unpack_from(message)
    return op, channel, bytes(message[FRAME.size :])


# ---- The source: threads, as the move's task ----


class SourceTunnel:
    """Accepts on the source daemon's three listening sockets (their fds, site_live.KINDS' order) and carries each
    connection to the target through one WebSocket on `sock`, an SSL socket already connected and pinned."""

    def __init__(self, sock, host, port, path, token, listeners):
        from websockets.sync.client import connect

        self.ws = connect(
            f"ws://{host}:{port}{path}",
            sock=sock,
            additional_headers={"Authorization": f"Bearer {token}"},
            proxy=None,
            compression=None,
            max_size=FRAME.size + CHUNK,
            open_timeout=30,
        )  # the TLS is the pinned socket's own: websockets speaks HTTP over it
        self.listeners = [socket.socket(fileno=fd) for fd in listeners]
        self.channels, self._next, self._lock, self.error = {}, 1, threading.Lock(), None
        self._stop = threading.Event()

    def start(self):
        for kind, listener in enumerate(self.listeners):
            threading.Thread(
                target=self._accept, args=(kind, listener), daemon=True, name=f"tunnel-{KINDS[kind]}"
            ).start()
        threading.Thread(target=self._from_target, daemon=True, name="tunnel-in").start()
        return self

    def _accept(self, kind, listener):
        listener.settimeout(1)
        while not self._stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                channel, self._next = self._next, self._next + 1
                self.channels[channel] = conn
            try:
                self.ws.send(frame(OPEN, channel, bytes([kind])))
            except Exception as e:  # the tunnel is gone
                self._fail(e)
                return
            threading.Thread(target=self._to_target, args=(channel, conn), daemon=True).start()

    def _to_target(self, channel, conn):
        try:
            while data := conn.recv(CHUNK):
                self.ws.send(frame(DATA, channel, data))
            self.ws.send(frame(EOF, channel))
        except OSError:
            with contextlib.suppress(Exception):
                self.ws.send(frame(CLOSE, channel))
        except Exception as e:
            self._fail(e)

    def _from_target(self):
        try:
            for message in self.ws:
                op, channel, payload = parse(message)
                with self._lock:
                    conn = self.channels.get(channel)
                if conn is None:
                    continue
                if op == DATA:
                    conn.sendall(payload)
                elif op == EOF:
                    with contextlib.suppress(OSError):
                        conn.shutdown(socket.SHUT_WR)
                elif op == CLOSE:
                    self._close_channel(channel)
        except Exception as e:
            if not self._stop.is_set():
                self._fail(e)

    def _close_channel(self, channel):
        with self._lock:
            conn = self.channels.pop(channel, None)
        if conn is not None:
            conn.close()

    def _fail(self, e):
        if self.error is None and not self._stop.is_set():
            self.error = str(e) or type(e).__name__
            logger.warning("Tunnel of a live migration stopped: %s", self.error)
        self.stop()

    def stop(self):
        self._stop.set()
        for listener in self.listeners:
            with contextlib.suppress(OSError):
                listener.close()
        with self._lock:
            channels, self.channels = list(self.channels.values()), {}
        for conn in channels:
            with contextlib.suppress(OSError):
                conn.close()
        with contextlib.suppress(Exception):
            self.ws.close()


# ---- The target: in the API's event loop (the WebSocket route) ----


async def serve(websocket, connect, make_filter, refused):
    """Carries the source's channels: `connect(kind)` (on a thread) gives this host's end of a new channel, as a
    connected socket; `make_filter()` the relay of a `libvirt` channel (site_relay.CallFilter); `refused(reason)`
    is told when the relay closes the tunnel."""
    from app.core import site_relay

    loop = asyncio.get_running_loop()
    channels = {}  # channel -> (socket, filter or None, task)

    async def back(channel, sock):
        try:
            while data := await loop.sock_recv(sock, CHUNK):
                await websocket.send_bytes(frame(DATA, channel, data))
            await websocket.send_bytes(frame(EOF, channel))
        except (OSError, RuntimeError):
            with contextlib.suppress(Exception):
                await websocket.send_bytes(frame(CLOSE, channel))

    try:
        while True:
            message = await websocket.receive_bytes()
            op, channel, payload = parse(message)
            if op == OPEN:
                if (
                    channel in channels
                    or len(channels) >= MAX_CHANNELS
                    or len(payload) != 1
                    or payload[0] >= len(KINDS)
                ):
                    raise ValueError("A channel the tunnel does not open")
                kind = KINDS[payload[0]]
                sock = await loop.run_in_executor(None, connect, kind)
                sock.setblocking(False)
                relay = make_filter() if kind == "libvirt" else None
                channels[channel] = (sock, relay, asyncio.ensure_future(back(channel, sock)))
                continue
            if channel not in channels:
                continue
            sock, relay, _ = channels[channel]
            if op == DATA:
                if relay is not None:
                    try:
                        payload = relay.feed(payload)
                    except site_relay.Refused as e:
                        refused(str(e))
                        return
                if payload:
                    await loop.sock_sendall(sock, payload)
            elif op == EOF:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_WR)
            elif op == CLOSE:
                sock.close()
                channels[channel][2].cancel()
                del channels[channel]
    finally:
        for sock, _relay, task in channels.values():
            task.cancel()
            with contextlib.suppress(OSError):
                sock.close()


def socket_from_fd(fd):
    """A socket object owning `fd` (one hyperlite-daemon handed over)."""
    return socket.socket(fileno=fd)


def close_fds(fds):
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)
