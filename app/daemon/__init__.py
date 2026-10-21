"""hyperlite-daemon: the operations that need root, behind a local socket (docs/design/privilege-separation.md).

The API (hyperlite-proxy, unprivileged once the split is done) asks for an operation by name with its arguments; the
daemon checks who is asking (the peer credentials of the socket), checks the arguments against the operation's model,
runs it as root and answers. There is no "run this command" and no "write this path": only the operations registered
in app/daemon/ops, each with typed arguments that it checks again itself.

The protocol is one JSON object per line on a Unix socket:

    request   {"op": "system.ping", "args": {}, "user": "alice", "request_id": "..."}
    answer    {"ok": true, "result": ...}  or  {"ok": false, "error": "<kind>", "detail": "<sentence>"}

The error kinds are the daemon's own (bad_request, forbidden, unknown_operation, invalid_arguments, failed), not the
codes of API v1: the API turns them into its own answers.
"""

SOCKET_ENV = "HYPERLITE_DAEMON_SOCKET"
DEFAULT_SOCKET = "/run/hyperlite/daemon.sock"
MAX_REQUEST_BYTES = 1 << 20  # one request line; bulk data never crosses the socket, file descriptors will
