"""Sessions (docs/design/privilege-separation.md, section 12): the daemon holds the key pairs that sign them and opens
one only after checking a proof itself (app/core/sign_in.py); the API verifies (app/core/tickets.py)."""

import hashlib
import threading
import time
from collections import deque
from typing import Any

from pydantic import Field

from app.core import peer_auth, sign_in
from app.daemon.registry import Args, DaemonError, operation

SECRET = 4096  # a password, a token: long enough for any, short enough to refuse a flood
CODE = 64
NAME = 64

# The daemon's own count of wrong proofs (GHSA-83f3-qj22-m659): the API's locks (app/routers/auth.py) are the first
# filter, but the API is the process this daemon must not trust, and it could ask at socket speed. Kept in this
# process's memory, out of the API's reach; looser than the API's, so a person only ever meets the API's.
FAILURES_MAX = 100  # per account, the API's own ceiling being 50
FAILURES_PER_TOKEN = 10  # second-factor codes tried with one pre-authentication token
FAILURES_WINDOW_S = 900


class _Failures:
    def __init__(self):
        self.lock = threading.Lock()
        self.seen = {}  # key -> deque of monotonic times

    def _recent(self, key, now):
        times = self.seen.get(key)
        while times and now - times[0] > FAILURES_WINDOW_S:
            times.popleft()
        if times is not None and not times:
            del self.seen[key]
            return 0
        return len(times or ())

    def locked(self, keys):
        now = time.monotonic()
        with self.lock:
            return any(self._recent(key, now) >= limit for key, limit in keys)

    def record(self, keys):
        now = time.monotonic()
        with self.lock:
            for key, _ in keys:
                self.seen.setdefault(key, deque()).append(now)


_failures = _Failures()
LOCKED = {"result": "locked", "detail": "Too many failed attempts: try again later"}


def _account(name):
    return (f"user:{(name or '').lower()}", FAILURES_MAX)


def _token(token):
    return (f"token:{hashlib.sha256(token.encode()).hexdigest()}", FAILURES_PER_TOKEN)


def _checked(keys, check):
    """`check()`'s answer, unless these keys failed too often; a wrong proof counts against each of them."""
    if _failures.locked(keys):
        return LOCKED
    answer = check()
    if answer.get("result") == "invalid":
        _failures.record([*keys, *([_account(answer["user"]["username"])] if answer.get("user") else [])])
    return answer


class PasswordArgs(Args):
    username: str = Field(max_length=256)
    password: str = Field(max_length=SECRET)
    remember: bool = False


class TotpArgs(Args):
    preauth_token: str = Field(max_length=SECRET)
    code: str = Field(max_length=CODE)


class WebAuthnArgs(Args):
    preauth_token: str = Field(max_length=SECRET)
    origin: str | None = Field(None, max_length=512)
    credential: dict[str, Any]


class SsoArgs(Args):
    handoff: str | None = Field(None, max_length=512)


class ChangePasswordArgs(Args):
    token: str = Field(max_length=SECRET)
    current_password: str = Field(max_length=SECRET)
    new_password: str = Field(max_length=SECRET)
    code: str | None = Field(None, max_length=CODE)


@operation("auth.password", PasswordArgs)
def password(args, ctx):
    """The sign-in form: the password checked here (local account, or the LDAP directory), then a session, or a
    pre-authentication token when the account has a second factor."""
    return _checked([_account(args.username)], lambda: sign_in.password(**args.model_dump()))


@operation("auth.local", Args)
def local(args, ctx):
    """root's session on this node, for hyperlite-admin (docs/design/admin-cli.md): only for a process the kernel says
    runs as root, never for the API's own user, whatever it claims."""
    if ctx.peer_uid != 0:
        raise DaemonError("forbidden", "Only root on this node gets its session")
    try:
        return sign_in.local_root()
    except PermissionError as e:
        raise DaemonError("forbidden", str(e)) from None


@operation("auth.totp", TotpArgs)
def totp(args, ctx):
    return _checked([_token(args.preauth_token)], lambda: sign_in.totp(**args.model_dump()))


@operation("auth.webauthn", WebAuthnArgs)
def webauthn(args, ctx):
    return _checked([_token(args.preauth_token)], lambda: sign_in.webauthn(**args.model_dump()))


@operation("auth.sso", SsoArgs)
def sso(args, ctx):
    return sign_in.sso(**args.model_dump())


@operation("auth.change_password", ChangePasswordArgs)
def change_password(args, ctx):
    return _checked([_token(args.token)], lambda: sign_in.change_password(**args.model_dump()))


class PeerArgs(Args):
    credential: str = Field(max_length=SECRET)
    targets: list[str] = Field(min_length=1, max_length=64)


class DelegateArgs(Args):
    credential: str = Field(max_length=SECRET)


@operation("auth.peer", PeerArgs)
def peer(args, ctx):
    """The tickets of calls to other members, for the account the credential of the request in progress proves
    (app/core/peer_auth.py): never for a name the API hands over."""
    if any(len(t) > NAME for t in args.targets):
        raise DaemonError("invalid_arguments", "A member's name is too long")
    try:
        return peer_auth.peer(args.credential, args.targets)
    except PermissionError as e:
        raise DaemonError("forbidden", str(e)) from None


@operation("auth.delegate", DelegateArgs)
def delegate(args, ctx):
    """A task ticket, for a run across the cluster that outlives the administrator's request (the rolling update)."""
    try:
        return peer_auth.delegate(args.credential)
    except PermissionError as e:
        raise DaemonError("forbidden", str(e)) from None
