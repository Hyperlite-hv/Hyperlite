"""A program in a pseudo-terminal, bridged to a WebSocket: the host shell (app/routers/host.py) and the root shell of
a container (app/routers/containers.py). Text frames are keystrokes; a frame starting with NUL carries the terminal
size as JSON ({"cols", "rows"})."""

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import termios

from fastapi import WebSocket, WebSocketDisconnect


def set_pty_size(fd, cols, rows):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _new_session():
    """In the child: a session of its own whose controlling terminal is the pty, so the shell has real terminal
    control (Ctrl+C, job control, `top`, `vim`) instead of staying attached to uvicorn's process group. Without
    TIOCSCTTY, bash starts with "no job control in this shell"."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


async def run(websocket: WebSocket, argv, env):
    """Run argv in a new pty until it exits or the WebSocket closes, then end its whole session."""
    master_fd, slave_fd = pty.openpty()
    set_pty_size(master_fd, 80, 24)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=_new_session,
            env=env,
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
                        set_pty_size(master_fd, int(dims["cols"]), int(dims["rows"]))
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
