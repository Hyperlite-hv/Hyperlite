"""What the API asks hyperlite-daemon to do as root (docs/design/privilege-separation.md, step 3): one operation, by
name, with its arguments, checked again by the daemon (app/daemon/registry.py). The API keeps who may ask (its routes'
permissions), the audit log and the tasks; the daemon does the root part.

HYPERLITE_DAEMON_INPROCESS=1 runs the operation in this process, through the same registry and its argument models:
the test suite, the e2e backend and the scale lab, which run no daemon. A node never sets it."""

import logging
import os
import time

from fastapi import HTTPException

from app.core import daemon_client
from app.core.audit import log_action
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

INPROCESS_ENV = "HYPERLITE_DAEMON_INPROCESS"

# A route's answer to the daemon's kinds (app/daemon/__init__.py), with the statuses of docs/api.md. A refusal's status
# is the route's own: what it answered before its domain moved. A failure of the daemon is this node's (500), never
# 502, which says another member of the cluster failed. A server outside this node refusing (an SMB server, an iSCSI
# target) is the 502 the storage routes gave it before their domain moved.
# "forbidden": what the daemon refused the caller (app/daemon/needs.py, step 4d), as the routes refuse it.
STATUS = {"forbidden": 403, "not_found": 404, "in_use": 409, "unreachable": 502, "unavailable": 503}


def run(op, user=None, timeout=daemon_client.TIMEOUT_S, **args):
    """The result of `op` with `args`, on behalf of `user` (named in the daemon's log). Raises DaemonError: its kind
    "refused" for a request the operation turned down (the message is for the user), "unavailable" without the
    daemon, another for a failure."""
    result, fds = run_fds(op, user=user, timeout=timeout, **args)
    daemon_client.close_fds(fds)
    return result


def run_fds(op, user=None, timeout=daemon_client.TIMEOUT_S, **args):
    """run(), and the file descriptors the operation hands over (a session's terminal or socket, the consoles
    domain): the caller's to close."""
    return _call(op, user, timeout, args)


def write(op, **args):
    """A write of the permissions (app/repositories/daemon_write.py): run() under a name of its own, which a test
    standing in for the daemon's other operations leaves alone."""
    result, fds = _call(op, None, daemon_client.TIMEOUT_S, args)
    daemon_client.close_fds(fds)
    return result


# What a task's log shows of each operation it asks for (app/core/tasks.py current_task): the objects, places and
# sizes it names, never a secret (a password, a token, a key, a session, a code), which only these names can be.
_LOGGED_ARGS = (
    "vm", "name", "new_name", "node", "target", "template", "container", "pool", "volume", "store", "network", "mac",
    "device", "dev", "path", "size_gb", "memory_mb", "vcpu", "action", "kind", "force", "start", "autostart",
    "snapshot", "backup_id", "check_only",
)  # fmt: skip
# The cluster's own plumbing (proofs, the replicated configuration): too frequent to say anything in a task's log.
_QUIET = ("auth.", "cfs.")


def _summary(args):
    shown = [
        f"{k}={str(v)[:60]}"
        for k in _LOGGED_ARGS
        if isinstance((v := args.get(k)), (str, int, float, bool)) and v != ""
    ]
    return f" ({', '.join(shown)})" if shown else ""


def _note(task_id, message):
    """A line of the task's log, never at the cost of the operation it describes."""
    from app.core import tasks

    try:
        tasks.task_log(task_id, message)
    except Exception:
        logger.warning("Task %s: its log could not take a line", task_id, exc_info=True)


def _call(op, user, timeout, args):
    from app.core import tasks

    task_id = None if op.startswith(_QUIET) else tasks.current_task()
    if task_id is None:
        return _dispatch(op, user, timeout, args)
    _note(task_id, f"hyperlite-daemon: {op}{_summary(args)}")
    began = time.monotonic()
    try:
        answer = _dispatch(op, user, timeout, args)
    except DaemonError as e:
        _note(task_id, f"{op}: {e.kind} after {time.monotonic() - began:.1f} s: {e.detail}")
        raise
    except Exception as e:
        _note(task_id, f"{op}: failed after {time.monotonic() - began:.1f} s: {type(e).__name__}: {e}")
        raise
    _note(task_id, f"{op}: done in {time.monotonic() - began:.1f} s")
    return answer


def _dispatch(op, user, timeout, args):
    if os.environ.get(INPROCESS_ENV) != "1":
        return daemon_client.call_fds(op, args, user=user, timeout=timeout)
    import app.daemon.ops  # noqa: F401  (registers the operations)
    from app.daemon.registry import Context, dispatch

    answer = dispatch(
        {"op": op, "args": args}, Context(user, None, os.getpid(), os.getuid(), proof=daemon_client._proof())
    )
    if not answer.get("ok"):
        raise DaemonError(answer.get("error") or "failed", answer.get("detail") or f"{op} failed")
    return answer.get("result"), answer.get("fds") or []


def http_error(e, refused=422, failed="{}"):
    """The HTTPException for the DaemonError `e`: what the operation refused, or arguments it does not take, `refused`
    with its reason; what does not exist, 404; what is in use, 409; a server outside this node refusing, 502; without
    the daemon, 503; any other failure, 500 with `failed` around its reason."""
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
