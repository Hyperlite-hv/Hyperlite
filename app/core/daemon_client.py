"""The API's side of hyperlite-daemon (app/daemon): one call, one operation, its answer or a DaemonError."""

import contextlib
import json
import os
import socket
import uuid

from app.daemon import DEFAULT_SOCKET, SOCKET_ENV
from app.daemon.registry import DaemonError

TIMEOUT_S = 30
# The descriptors one answer may carry (app/daemon/registry.py, Handover): a session's terminal or socket.
MAX_FDS = 4
_CHUNK = 65536


def _proof():
    """The credential of the request this call serves (the session or API token it came with, peer_auth.CREDENTIAL):
    hyperlite-daemon checks who asks itself (docs/design/privilege-separation.md, section 14)."""
    from app.core import peer_auth

    return peer_auth.CREDENTIAL.get()


def close_fds(fds):
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)


def _read_answer(sock):
    """The answer's line and the descriptors that came with it."""
    data, fds = b"", []
    while not data.endswith(b"\n"):
        chunk, received, _flags, _addr = socket.recv_fds(sock, _CHUNK, MAX_FDS)
        fds += received
        if not chunk:
            break
        data += chunk
    return data, fds


def call(op, args=None, user=None, timeout=TIMEOUT_S):
    """Ask hyperlite-daemon for `op` with `args`, on behalf of `user` (for its log). A long operation answers at once
    with a task id, as the API does today, so the timeout only covers starting it."""
    result, fds = call_fds(op, args, user, timeout)
    close_fds(fds)  # descriptors nobody asked for are never left open
    return result


def call_fds(op, args=None, user=None, timeout=TIMEOUT_S):
    """call(), and the file descriptors the answer carries (a session's terminal or socket), which are the caller's to
    close."""
    path = os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET
    request = {"op": op, "args": args or {}, "user": user, "request_id": uuid.uuid4().hex, "proof": _proof()}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            # The daemon may refuse this process before reading the request: its answer is still there to read.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                sock.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            line, fds = _read_answer(sock)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonError("unavailable", "hyperlite-daemon is not running") from e
    except TimeoutError as e:
        raise DaemonError("unavailable", f"hyperlite-daemon did not answer within {timeout:g} s") from e
    try:
        answer = json.loads(line)
    except ValueError as e:
        close_fds(fds)
        raise DaemonError("bad_request", "hyperlite-daemon gave an unreadable answer") from e
    if not answer.get("ok"):
        close_fds(fds)
        raise DaemonError(answer.get("error") or "failed", answer.get("detail") or "hyperlite-daemon refused")
    return answer.get("result"), fds
