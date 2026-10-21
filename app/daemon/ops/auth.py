"""Sessions (docs/design/privilege-separation.md, section 12): the daemon holds the key pairs that sign them and opens
one only after checking a proof itself (app/core/sign_in.py); the API verifies (app/core/tickets.py)."""

from typing import Any

from pydantic import Field

from app.core import sign_in
from app.daemon.registry import Args, operation

SECRET = 4096  # a password, a token: long enough for any, short enough to refuse a flood
CODE = 64


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
