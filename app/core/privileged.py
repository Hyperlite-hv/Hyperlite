"""What the API asks hyperlite-daemon to do as root (docs/design/privilege-separation.md, step 3): one operation, by
name, with its arguments, checked again by the daemon (app/daemon/registry.py). The API keeps who may ask (its routes'
permissions), the audit log and the tasks; the daemon does the root part.

HYPERLITE_DAEMON_INPROCESS=1 runs the operation in this process, through the same registry and its argument models:
the test suite, the e2e backend and the scale lab, which run no daemon. A node never sets it."""

import os

from app.core import daemon_client
from app.daemon.registry import DaemonError

INPROCESS_ENV = "HYPERLITE_DAEMON_INPROCESS"


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
