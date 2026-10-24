"""hyperlite-daemon: the operations that need root, behind a local socket (docs/design/privilege-separation.md).

The API (hyperlite-proxy, unprivileged once the split is done) asks for an operation by name with its arguments; the
daemon checks who is asking (the peer credentials of the socket), checks the arguments against the operation's model,
runs it as root and answers. There is no "run this command" and no "write this path": only the operations registered
in app/daemon/ops, each with typed arguments that it checks again itself.

The protocol is one JSON object per line on a Unix socket:

    request   {"op": "system.ping", "args": {}, "user": "alice", "request_id": "..."}
    answer    {"ok": true, "result": ...}  or  {"ok": false, "error": "<kind>", "detail": "<sentence>"}

The error kinds are the daemon's own, not the codes of API v1 (registry.KINDS):

    invalid_arguments  the arguments do not match the operation's model (an unknown field, a length, a type)
    unknown_operation  no operation by that name
    bad_request        a request, or an answer, that is not one JSON object
    forbidden          not for this process (the peer credentials), or a proof the daemon does not accept
    refused            the operation turned the request down: its sentence is for the user, as the module wrote it
    not_found          what the request names does not exist
    in_use             what the request names is in use (a volume a VM uses): nothing was done
    unreachable        a server outside this node the operation needs (an SMB server, an iSCSI target) refused it
    failed             the operation failed: its sentence names the cause, or the daemon's log has it
    unavailable        the API's side only (app/core/daemon_client.py): the daemon does not answer

A route turns them into its answer through app/core/privileged.py (`ask`, `http_error`): `refused` and
`invalid_arguments` the route's own refusal status, `not_found` 404, `in_use` 409, `unreachable` 502 (as the storage
routes answered it before), `unavailable` 503, the others 500.
"""

SOCKET_ENV = "HYPERLITE_DAEMON_SOCKET"
DEFAULT_SOCKET = "/run/hyperlite/daemon.sock"
MAX_REQUEST_BYTES = 1 << 20  # one request line; bulk data never crosses the socket, file descriptors will
