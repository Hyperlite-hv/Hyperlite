"""Sessions (docs/design/privilege-separation.md, section 12): the daemon holds the key pairs that sign them and opens
one only after checking a proof itself (app/core/sign_in.py); the API verifies (app/core/tickets.py)."""

from typing import Any

from pydantic import Field

from app.core import peer_auth, sign_in
from app.daemon.registry import Args, DaemonError, operation

SECRET = 4096  # a password, a token: long enough for any, short enough to refuse a flood
CODE = 64
NAME = 64


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
    return sign_in.password(**args.model_dump())


@operation("auth.local", Args)
def local(args, ctx):
    """root's session on this node, for hyperlite-admin (docs/design/admin-cli.md): only for a process the kernel says
    runs as root, never for the API's own user, whatever it claims."""
    if ctx.peer_uid != 0:
        raise DaemonError("forbidden", "Only root on this node gets its session")
    return sign_in.local_root()


@operation("auth.totp", TotpArgs)
def totp(args, ctx):
    return sign_in.totp(**args.model_dump())


@operation("auth.webauthn", WebAuthnArgs)
def webauthn(args, ctx):
    return sign_in.webauthn(**args.model_dump())


@operation("auth.sso", SsoArgs)
def sso(args, ctx):
    return sign_in.sso(**args.model_dump())


@operation("auth.change_password", ChangePasswordArgs)
def change_password(args, ctx):
    return sign_in.change_password(**args.model_dump())


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
