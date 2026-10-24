"""What the API asks hyperlite-daemon to do as root (docs/design/privilege-separation.md, step 3): one operation, by
name, with its arguments, checked again by the daemon (app/daemon/registry.py). The API keeps who may ask (its routes'
permissions), the audit log and the tasks; the daemon does the root part.

HYPERLITE_DAEMON_INPROCESS=1 runs the operation in this process, through the same registry and its argument models:
the test suite, the e2e backend and the scale lab, which run no daemon. A node never sets it."""

import os

from fastapi import HTTPException

from app.core import daemon_client
from app.core.audit import log_action
from app.daemon.registry import DaemonError

INPROCESS_ENV = "HYPERLITE_DAEMON_INPROCESS"

# A route's answer to the daemon's kinds (app/daemon/__init__.py), with the statuses of docs/api.md. A refusal's status
# is the route's own: what it answered before its domain moved. A failure of the daemon is this node's (500), never
# 502, which says another member of the cluster failed.
STATUS = {"not_found": 404, "in_use": 409, "unavailable": 503}


def run(op, user=None, timeout=daemon_client.TIMEOUT_S, **args):
    """The result of `op` with `args`, on behalf of `user` (named in the daemon's log). Raises DaemonError: its kind
    "refused" for a request the operation turned down (the message is for the user), "unavailable" without the
    daemon, another for a failure."""
    if os.environ.get(INPROCESS_ENV) != "1":
        return daemon_client.call(op, args, user=user, timeout=timeout)
    import app.daemon.ops  # noqa: F401  (registers the operations)
    from app.daemon.registry import Context, dispatch

    answer = dispatch({"op": op, "args": args}, Context(user, None, os.getpid(), os.getuid()))
    if not answer.get("ok"):
        raise DaemonError(answer.get("error") or "failed", answer.get("detail") or f"{op} failed")
    return answer.get("result")


def http_error(e, refused=422, failed="{}"):
    """The HTTPException for the DaemonError `e`: what the operation refused, or arguments it does not take, `refused`
    with its reason; what does not exist, 404; what is in use, 409; without the daemon, 503; any other failure, 500
    with `failed` around its reason."""
    if e.kind in ("refused", "invalid_arguments"):
        return HTTPException(status_code=refused, detail=e.detail)
    if e.kind in STATUS:
        return HTTPException(status_code=STATUS[e.kind], detail=e.detail)
    return HTTPException(status_code=500, detail=failed.format(e.detail))


def ask(op, user, action, target, /, *, refused=422, failed="{}", **args):
    """`op` run for a route, on behalf of the account `user`: its result, or the HTTPException of http_error, the
    failure written into the audit log as `action` on `target`. The routes keep their permission checks and their
    success's audit entry."""
    try:
        return run(op, user=user["username"], **args)
    except DaemonError as e:
        log_action(user["username"], action, target, "failed", e.detail)
        raise http_error(e, refused=refused, failed=failed) from None
