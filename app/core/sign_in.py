"""Opening a session: what hyperlite-daemon checks before it signs one (docs/design/privilege-separation.md, section 12,
PR B), as Proxmox VE's ticket is made by the process that checked the password.

Each function checks a proof (a password, a second factor, the one-time code an SSO sign-in left, the current password
for a change) and only then signs, with the key only the daemon reads (app/core/tickets.py). The API asks for them
by name (ask); it keeps what does not open a session: the lock after repeated failures, the audit log, its answers.

Every function answers a dict: {"result": "session", "token", "user"}, {"result": "second_factor", "preauth_token",
"methods", "user"}, or {"result": <a refusal>, "detail"?}. "user" holds what the API answers with: username, role,
password_change_required."""

import os
import time
from datetime import timedelta

from app.core import tickets

OPERATIONS = ("password", "totp", "webauthn", "sso", "change_password", "sso_start", "sso_callback")
PREAUTH_LIFETIME_S = 5 * 60  # enough to type a code
LOCAL_ROOT_LIFETIME_S = 5 * 60


def ask(op, log_user=None, **args):
    """The answer of `op`, from hyperlite-daemon; in this process with HYPERLITE_AUTH_SIGNING=local (tests). Raises
    DaemonError when the daemon does not answer. `log_user`: the account the daemon's log names, when the arguments do
    not."""
    if op not in OPERATIONS:
        raise ValueError(op)
    if os.environ.get(tickets.SIGNING_ENV) == "local":
        return globals()[op](**args)
    from app.core import daemon_client

    return daemon_client.call(f"auth.{op}", args, user=log_user or args.get("username"))


def _public(user, must_change=None):
    return {
        "username": user["username"],
        "role": user["role"],
        "password_change_required": bool(user.get("must_change_password") if must_change is None else must_change),
    }


def _sign(claims, lifetime):
    from app.core import security

    return tickets.sign_local(security._claims(claims, lifetime))


def _session(user, remember, must_change=None):
    from app.core.security import ACCESS_TOKEN_EXPIRE_MINUTES, REMEMBER_TOKEN_EXPIRE_DAYS

    lifetime = (
        timedelta(days=REMEMBER_TOKEN_EXPIRE_DAYS) if remember else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    token = _sign({"sub": user["username"], "role": user["role"]}, lifetime)
    return {"result": "session", "token": token, "user": _public(user, must_change)}


def _signed_in(user, remember, must_change=None):
    """_session() for a sign-in (not a password change's fresh token), its time recorded here: the accounts are the
    daemon's to write (step 4b), and the API, which signs nobody in, never writes them."""
    from datetime import UTC, datetime

    from app.repositories import registry

    registry.accounts().sync.record_login(user["username"], datetime.now(UTC).isoformat())
    return _session(user, remember, must_change)


def local_root():
    """root's session on this node, for hyperlite-admin (docs/design/admin-cli.md): what hyperlite-daemon signs once the
    kernel told it the caller runs as root (app/daemon/ops/auth.py). Short, so a ticket left in a process's memory soon
    proves nothing; the CLI asks again during a long wait."""
    from app.core import admin_cli, security, self_node

    if admin_cli.locked():
        raise PermissionError(admin_cli.REASON)
    claims = {"sub": security.local_root_name(self_node.name()), "role": "admin", security.LOCAL_ROOT_CLAIM: True}
    token = _sign(claims, timedelta(seconds=LOCAL_ROOT_LIFETIME_S))
    return {"token": token, "expires_in_s": LOCAL_ROOT_LIFETIME_S}


def _second_factor_methods(user):
    from app.core import webauthn_keys

    return [
        m for m, on in (("totp", user["totp_enabled"]), ("webauthn", webauthn_keys.has_keys(user["username"]))) if on
    ]


def _preauth(user, remember, must_change=None):
    """Correct password (or SSO identity), a second factor set up: a 5-minute token that only proves that much, marked
    2fa_pending, which no endpoint takes as a session (security._session_user)."""
    token = tickets.sign_local(
        {
            "sub": user["username"],
            "2fa_pending": True,
            "remember": bool(remember),
            "exp": int(time.time()) + PREAUTH_LIFETIME_S,
        }
    )
    return {
        "result": "second_factor",
        "preauth_token": token,
        "methods": _second_factor_methods(user),
        "user": _public(user, must_change),
    }


def password(username, password, remember=False):
    """The sign-in form. "weak": the password no longer meets the policy, so the account must change it first."""
    from app.core.password_policy import password_problem
    from app.core.security import authenticate_user, flag_weak_password

    user = authenticate_user(username, password)
    if not user:
        return {"result": "invalid"}
    weak = user.get("auth_source", "local") == "local" and bool(password_problem(password, user["username"]))
    newly_weak = weak and not user.get("must_change_password")
    if newly_weak:
        flag_weak_password(user["username"])
    must_change = bool(user.get("must_change_password")) or weak
    answer = (
        _preauth(user, remember, must_change)
        if _second_factor_methods(user)
        else _signed_in(user, remember, must_change)
    )
    answer["weak_flagged"] = newly_weak
    return answer


def _pending(preauth_token):
    """(user, claims) of a valid pre-authentication token, or None."""
    from app.core.security import decode_token, get_user

    claims = decode_token(preauth_token)
    if not claims or not claims.get("2fa_pending") or not claims.get("sub"):
        return None
    user = get_user(claims["sub"])
    return (user, claims) if user else None


def totp(preauth_token, code):
    from app.core.twofa import verify_code

    pending = _pending(preauth_token)
    if pending is None:
        return {"result": "expired"}
    user, claims = pending
    if not verify_code(user["username"], user["totp_secret"], code):
        return {"result": "invalid", "user": _public(user)}
    return _signed_in(user, bool(claims.get("remember")))


def webauthn(preauth_token, origin, credential):
    from app.core import webauthn_keys

    pending = _pending(preauth_token)
    if pending is None:
        return {"result": "expired"}
    user, claims = pending
    try:
        ok = webauthn_keys.authenticate(user["username"], origin, credential)
    except webauthn_keys.WebAuthnError as e:
        return {"result": "refused", "status": e.status, "detail": e.message, "user": _public(user)}
    if not ok:
        return {"result": "invalid", "user": _public(user)}
    return _signed_in(user, bool(claims.get("remember")))


def sso(handoff):
    """The session an SSO sign-in leads to, from the one-time code its callback left (app/routers/sso.py). The identity
    provider proves who signs in, not the second factor this account chose to require."""
    from app.core import sso as sso_core
    from app.core.security import get_user

    username = sso_core.consume_handoff(handoff)
    user = get_user(username) if username else None
    if user is None:
        return {"result": "expired"}
    return _preauth(user, False) if _second_factor_methods(user) else _signed_in(user, False)


def change_password(token, current_password, new_password, code=None):
    """An account changes its own password: the session, the current password and the second factor when it has one
    are checked here. Every other session and every API token of the account is revoked; the session making the change
    gets a fresh token with the same lifetime."""
    from fastapi import HTTPException

    from app.core.api_tokens import revoke_all_tokens
    from app.core.password_policy import password_problem
    from app.core.security import _session_user, decode_token, session_remembered, set_password, verify_password
    from app.core.twofa import verify_code

    claims = decode_token(token)
    try:
        user = _session_user(claims, pending_ok=True) if claims else None
    except HTTPException:
        user = None
    if user is None:
        return {"result": "expired"}
    if user.get("auth_source") in ("sso", "ldap"):
        return {"result": "external", "user": _public(user)}
    if not verify_password(current_password, user["hashed_password"]):
        return {"result": "invalid", "detail": "Incorrect current password", "user": _public(user)}
    if user["totp_enabled"] and not verify_code(user["username"], user["totp_secret"], code or ""):
        return {
            "result": "invalid",
            "detail": "Invalid 2FA code (a code is accepted once: wait for the next one)",
            "user": _public(user),
        }
    if new_password == current_password:
        return {"result": "unchanged"}
    problem = password_problem(new_password, user["username"])
    if problem:
        return {"result": "weak", "detail": problem}
    remember = session_remembered(token)
    set_password(user["username"], new_password)
    revoked = revoke_all_tokens(user["username"])
    answer = _session({**user, "must_change_password": 0}, remember, must_change=False)
    answer["revoked_tokens"] = revoked
    return answer


# --- SSO: the sign-in's start and the identity provider's callback (app/routers/sso.py) ---
#
# Both need the client secret, which only the daemon opens, and the callback makes or updates the account: the daemon's
# to write. The API keeps the browser's side (the redirects, the cookies) and the audit log.


def sso_start():
    """{"result": "redirect", "url", "binding"}: where the browser goes, and the value its HttpOnly cookie binds the
    callback to; or {"result": "refused", "status", "detail"}."""
    import logging

    from app.core import sso as core

    config = core.get_config()
    if not config or not config["enabled"]:
        return {"result": "refused", "status": 400, "detail": "SSO is not enabled"}
    if config.get("client_secret_unreadable"):
        return {
            "result": "refused",
            "status": 503,
            "detail": "The stored SSO client secret cannot be decrypted (the encryption key changed): enter it again",
        }
    if not (config["issuer"] and config["client_id"] and config["client_secret"] and config["redirect_uri"]):
        return {"result": "refused", "status": 400, "detail": "Incomplete SSO configuration"}
    try:
        doc = core.discover(config["issuer"])
    except Exception as e:
        # Asked without any session: the error's own words (an internal address, TLS details) go to the log only.
        logging.getLogger(__name__).warning("SSO discovery of %s failed: %s", config["issuer"], e)
        return {"result": "refused", "status": 502, "detail": "The identity provider cannot be reached"}
    state, nonce, binding = core.create_state()
    return {"result": "redirect", "url": core.build_authorize_url(config, doc, state, nonce), "binding": binding}


def _sso_refused(detail, log=None, log_user="system"):
    return {"result": "refused", "detail": detail, "log": log, "log_user": log_user}


def sso_callback(state, binding, code):
    """The identity provider's answer checked (state bound to this browser, code exchanged, identity token
    validated), the account made or updated, and a one-time handoff code for the dashboard's exchange:
    {"result": "handoff", "handoff", "username"}; or {"result": "refused", "detail" (for the browser), "log" (for the
    audit log, or None), "log_user"}."""
    from jwt import PyJWTError

    from app.core import sso as core

    nonce = core.consume_state(state, binding)
    if nonce is None:
        # Expired, replayed, or started from another browser (a callback link someone sent).
        return _sso_refused("Login session expired, try again", "SSO: unknown, expired or foreign sign-in state")
    config = core.get_config()
    if not config or not config["enabled"]:
        return _sso_refused("SSO is disabled")
    try:
        doc = core.discover(config["issuer"])
        tokens = core.exchange_code(config, doc, code)
        claims = core.validate_id_token(config, doc, tokens["id_token"], nonce)
    except (PyJWTError, KeyError) as e:
        return _sso_refused("Invalid identity token", f"SSO: invalid identity token ({e})")
    except Exception as e:
        return _sso_refused("Unable to contact the IdP", f"SSO: IdP unreachable or invalid response ({e})")
    username = core.resolve_username(claims)
    if not username:
        return _sso_refused("The IdP provided no usable identifier")
    role = core.resolve_role(config, claims)
    try:
        row = core.provision_user(username, role, subject=claims.get("sub"))
    except core.LocalAccountConflict:
        return _sso_refused(
            "This username already matches a local account", "SSO: this name already matches a local account", username
        )
    except core.SubjectConflict:
        return _sso_refused(
            "This username belongs to another identity: ask an administrator",
            "SSO: this name belongs to another identity of the provider",
            username,
        )
    return {"result": "handoff", "handoff": core.create_handoff(row["username"]), "username": row["username"]}
