"""The fixed list of operations hyperlite-daemon runs, and how one request is checked and dispatched."""

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
class Context:
    """Who asked: the user the API names (for the log and the audit) and the process at the other end of the socket."""

    user: str | None
    request_id: str | None
    peer_pid: int
    peer_uid: int


OPERATIONS = {}


def operation(name, args=NoArgs):
    """Register a function as the operation `name`, its arguments checked against `args` before it runs."""

    def register(fn):
        if name in OPERATIONS:
            raise ValueError(f"operation {name} registered twice")
        OPERATIONS[name] = (args, fn)
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
    try:
        result = fn(args, ctx)
    except DaemonError as e:
        logger.warning("%s by %s refused: %s", op, ctx.user or "?", e.detail)
        return _error(e.kind, e.detail)
    except Exception:
        logger.exception("%s by %s failed", op, ctx.user or "?")
        return _error("failed", f"{op} failed (details in the daemon's log)")
    logger.info("%s by %s (pid %d)", op, ctx.user or "?", ctx.peer_pid)
    return {"ok": True, "result": result}


def _error(kind, detail):
    return {"ok": False, "error": kind, "detail": detail}
