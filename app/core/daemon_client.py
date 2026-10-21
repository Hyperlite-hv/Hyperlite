"""The API's side of hyperlite-daemon (app/daemon): one call, one operation, its answer or a DaemonError."""

import json
import os
import socket
import uuid

from app.daemon import DEFAULT_SOCKET, SOCKET_ENV
from app.daemon.registry import DaemonError

TIMEOUT_S = 30


def call(op, args=None, user=None, timeout=TIMEOUT_S):
    """Ask hyperlite-daemon for `op` with `args`, on behalf of `user` (for its log). A long operation answers at once
    with a task id, as the API does today, so the timeout only covers starting it."""
    path = os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET
    request = {"op": op, "args": args or {}, "user": user, "request_id": uuid.uuid4().hex}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            line = sock.makefile("rb").readline()
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonError("unavailable", "hyperlite-daemon is not running") from e
    except TimeoutError as e:
        raise DaemonError("unavailable", f"hyperlite-daemon did not answer within {timeout:g} s") from e
    try:
        answer = json.loads(line)
    except ValueError as e:
        raise DaemonError("bad_request", "hyperlite-daemon gave an unreadable answer") from e
    if not answer.get("ok"):
        raise DaemonError(answer.get("error") or "failed", answer.get("detail") or "hyperlite-daemon refused")
    return answer.get("result")
