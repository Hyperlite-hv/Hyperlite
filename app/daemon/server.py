"""The socket of hyperlite-daemon: who may connect, and one request per line."""

import contextlib
import grp
import json
import logging
import os
import pwd
import socket
import socketserver
import struct
from pathlib import Path

from app.daemon import DEFAULT_SOCKET, MAX_REQUEST_BYTES, SOCKET_ENV
from app.daemon.registry import Context, dispatch

logger = logging.getLogger(__name__)

# The account the API runs as once it drops root (step 4); root is accepted too until then.
API_USER = "hyperlite"


def allowed_uids():
    uids = {0}
    with contextlib.suppress(KeyError):  # not created yet: root only
        uids.add(pwd.getpwnam(API_USER).pw_uid)
    return uids


def peer_credentials(sock):
    """(pid, uid, gid) of the process at the other end of a Unix socket, as the kernel knows it."""
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def _answer(handler, payload):
    handler.wfile.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
    handler.wfile.flush()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        pid, uid, _gid = peer_credentials(self.request)
        if uid not in self.server.allowed_uids():
            logger.warning("connection refused from pid %d, uid %d", pid, uid)
            _answer(self, {"ok": False, "error": "forbidden", "detail": "This process may not use hyperlite-daemon"})
            return
        while True:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_REQUEST_BYTES:
                _answer(self, {"ok": False, "error": "bad_request", "detail": "Request too large"})
                return
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError
            except ValueError:
                _answer(self, {"ok": False, "error": "bad_request", "detail": "A request is one JSON object per line"})
                continue
            user = request.get("user") if isinstance(request.get("user"), str) else None
            rid = request.get("request_id") if isinstance(request.get("request_id"), str) else None
            _answer(self, dispatch(request, Context(user=user, request_id=rid, peer_pid=pid, peer_uid=uid)))


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allowed_uids = staticmethod(allowed_uids)


def serve(path=None):
    """Listen on the socket until stopped: 0660, owned by root and the API's group when it exists."""
    import app.daemon.ops  # noqa: F401  (registers the operations)

    path = Path(path or os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    server = Server(str(path), Handler)
    try:
        gid = grp.getgrnam(API_USER).gr_gid
    except KeyError:
        gid = 0
    os.chown(path, 0, gid)
    os.chmod(path, 0o660)
    logger.info("hyperlite-daemon listening on %s", path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        path.unlink(missing_ok=True)
