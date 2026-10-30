"""A terminal session hyperlite-daemon opens (app/daemon/ops/console.py), bridged to a WebSocket: the host shell
(app/routers/host.py), a container's root shell and terminal (app/routers/containers.py), a VM's serial console and
terminal (app/routers/vms/console.py). The daemon runs the program as root in a new pseudo-terminal and hands its
master here; the API keeps the ticket, the audit log and this bridge. Text frames are keystrokes; a frame starting with
NUL carries the terminal size as JSON ({"cols", "rows"})."""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import struct
import termios
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core import daemon_client, privileged
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)


def set_pty_size(fd, cols, rows):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def say(websocket: WebSocket, text):
    """A line of Hyperlite's own in the terminal, in red."""
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] {text}\x1b[0m\r\n")


async def bridge(websocket: WebSocket, master_fd):
    """Relay the terminal and the WebSocket both ways until the program ends or the WebSocket closes, then close the
    terminal: the program's session hangs up."""
    os.set_blocking(master_fd, False)
    loop = asyncio.get_running_loop()
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
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        set_pty_size(master_fd, int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        logger.debug("Unreadable terminal size", exc_info=True)
                else:
                    os.write(master_fd, msg.encode())

    tasks = {asyncio.ensure_future(pty_to_ws()), asyncio.ensure_future(ws_to_pty())}
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        with contextlib.suppress(ValueError, OSError):
            loop.remove_reader(master_fd)
        with contextlib.suppress(OSError):
            os.close(master_fd)


@dataclass
class Session:
    """A terminal session hyperlite-daemon opened: the operation's result and the terminal's master."""

    result: dict
    fd: int
    username: str | None

    async def serve(self, websocket: WebSocket):
        """Bridge the terminal to the WebSocket, then end the session: the program's exit status, None when unknown."""
        try:
            await bridge(websocket, self.fd)
        finally:
            status = await end(self.result.get("session"), self.username)
        return status


async def open_session(op, username, **args):
    """Ask hyperlite-daemon for the terminal session `op` (app/daemon/ops/console.py). Raises DaemonError when it
    refused it, its reason for the terminal."""
    result, fds = await run_in_threadpool(privileged.run_fds, op, user=username, **args)
    if not fds:
        await end(result.get("session"), username)
        raise DaemonError("failed", "The terminal could not be opened")
    daemon_client.close_fds(fds[1:])
    return Session(result, fds[0], username)


async def end(session, username):
    """console.close for a session (None: nothing to end): its exit status, None when it is unknown."""
    if session is None:
        return None
    try:
        return (await run_in_threadpool(privileged.run, "console.close", user=username, session=session))["exit"]
    except DaemonError as e:
        logger.warning("Console session %s not closed: %s", session[:8], e.detail)
        return None
