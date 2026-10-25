"""The fixed list of operations hyperlite-daemon runs, and how one request is checked and dispatched."""

import contextvars
import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


class Args(BaseModel):
    """Base of every operation's arguments: unknown fields are refused, never ignored."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


# The error kinds, each described in app/daemon/__init__.py; tests/test_privileged.py checks that no code raises another.
KINDS = frozenset(
    {
        "invalid_arguments",
        "unknown_operation",
        "bad_request",
        "forbidden",
        "refused",
        "not_found",
        "in_use",
        "unreachable",
        "failed",
        "unavailable",
    }
)


class DaemonError(Exception):
    """A refusal or a failure, with its kind (KINDS) and a sentence for the person who asked."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class Handover:
    """An operation's result with open file descriptors for the API (a pseudo-terminal, a connected socket): the
    socket passes them with the answer (SCM_RIGHTS) and closes the daemon's copies; in process, they are the caller's
    to close."""

    result: object
    fds: list


@dataclass(frozen=True)
class Context:
    """Who asked: the user the API names (for the log and the audit), the process at the other end of the socket, and
    the caller's own credential (docs/design/privilege-separation.md, section 14: the daemon checks who asks)."""

    user: str | None
    request_id: str | None
    peer_pid: int
    peer_uid: int
    proof: str | None = None


OPERATIONS = {}

# Set while an operation runs: the code it calls is the daemon's, here or in process (app/repositories/daemon_write.py).
_running = contextvars.ContextVar("hyperlite_daemon_operation", default=None)


def running_here():
    """Whether this code runs inside one of the daemon's operations."""
    return _running.get() is not None


NEEDS = {}  # operation -> what it needs of the person who asks (app/daemon/needs.py); an administrator by default


def operation(name, args=NoArgs, needs=None):
    """Register a function as the operation `name`, its arguments checked against `args` before it runs, and its
    caller against `needs` (app/daemon/needs.py: an administrator unless it says otherwise)."""

    def register(fn):
        if name in OPERATIONS:
            raise ValueError(f"operation {name} registered twice")
        OPERATIONS[name] = (args, fn)
        if needs is not None:
            NEEDS[name] = needs
        return fn

    return register


def dispatch(request, ctx):
    """The answer to one decoded request (a dict), never an exception."""
    op = request.get("op")
    if not isinstance(op, str) or op not in OPERATIONS:
        return _error("unknown_operation", f"Unknown operation: {op!r}")
    model, fn = OPERATIONS[op]
    raw = request.get("args") or {}
    if not isinstance(raw, dict):
        return _error("invalid_arguments", "The arguments must be an object")
    try:
        args = model.model_validate(raw)
    except ValidationError as e:
        fields = ", ".join(".".join(str(p) for p in err["loc"]) or "?" for err in e.errors())
        return _error("invalid_arguments", f"Invalid arguments for {op}: {fields}")
    from app.daemon import needs

    try:
        needs.check(op, NEEDS.get(op, needs.ADMIN), ctx, args)
    except DaemonError as e:
        logger.warning("%s by %s refused: %s", op, ctx.user or "?", e.detail)
        return _error(e.kind, e.detail)
    running = _running.set(op)
    try:
        result = fn(args, ctx)
    except DaemonError as e:
        logger.warning("%s by %s refused: %s", op, ctx.user or "?", e.detail)
        return _error(e.kind, e.detail)
    except Exception:
        logger.exception("%s by %s failed", op, ctx.user or "?")
        return _error("failed", f"{op} failed (details in the daemon's log)")
    finally:
        _running.reset(running)
    logger.info("%s by %s (pid %d)%s", op, ctx.user or "?", ctx.peer_pid, _proven_note(ctx))
    if isinstance(result, Handover):
        return {"ok": True, "result": result.result, "fds": list(result.fds)}
    return {"ok": True, "result": result}


def proven(proof):
    """The account a caller's credential proves to this daemon, or None: a session or an API token of this node, a
    task ticket, or a ticket another member signed for this node (section 14, PR 4a: checked and logged, not yet
    required)."""
    if not proof:
        return None

    # Checking an API token records its last use, a write of the permissions (app/repositories/daemon_write.py): done
    # here, as the daemon's own, never asked of the daemon again, which would check this proof again, without end.
    account = proven_account(proof)
    return account["username"] if account else None


def proven_account(proof):
    """The account a caller's credential proves, as the API resolves it (a users row, or root of a node through
    hyperlite-admin, which has none, as root@pam on Proxmox VE), or None."""
    if not proof:
        return None
    from app.core import peer_auth, security
    from app.repositories import registry

    running = _running.set("proof")
    try:
        claims = peer_auth.verify(proof)
        if claims is None:
            return peer_auth._account(proof)
        if claims.get(security.LOCAL_ROOT_CLAIM):
            return security._local_root(claims)
        return registry.accounts().sync.get(claims["sub"])
    except (PermissionError, KeyError, TypeError, ValueError):
        return None
    except Exception as e:
        if getattr(e, "status_code", None) in (401, 403):  # the API's own refusal of a credential
            return None
        logger.warning("A credential could not be checked", exc_info=True)
        return None
    finally:
        _running.reset(running)


def _proven_note(ctx):
    """What the log says of the caller's proof: nothing when it proves the user the API named."""
    who = proven(ctx.proof)
    if who is None:
        return " [no proof]"
    if who != ctx.user:
        return f" [proof of {who}]"
    return ""


def _error(kind, detail):
    return {"ok": False, "error": kind, "detail": detail}
