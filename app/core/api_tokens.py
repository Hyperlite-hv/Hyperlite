"""API tokens: an authentication mechanism distinct from session JWTs,
designed for automation (scripts, Terraform, cron). They do not expire
quickly and can be revoked individually without affecting the web session.
Like a password, the plain token is NEVER stored, only its SHA-256 hash: it
cannot be recovered after creation and is shown only once in the UI."""

import hashlib
import secrets
from datetime import UTC, datetime

TOKEN_PREFIX = "hlt_"  # noqa: S105 -- public token prefix, not a credential


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _accounts():
    # The account repository's synchronous bridge: signing in runs in FastAPI dependencies and sync endpoints.
    from app.repositories import registry

    return registry.accounts().sync


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(username: str, name: str, expires_at: str | None = None, kind: str = "api"):
    """Return (id, plain_token). The plain token cannot be recovered once this
    function has returned. kind: "api" (created by hand, no expiry by default) or
    "cli" (a workstation signed in with `hyperlite login`, always expiring)."""
    token = generate_token()
    token_id = _accounts().create_token(username, name, _hash(token), datetime.now(UTC).isoformat(), expires_at, kind)
    return token_id, token


def list_tokens(username: str):
    return _accounts().list_tokens(username)


def revoke_token(username: str, token_id: int) -> bool:
    """Scoped to a username: a user can only revoke their own tokens, and
    guessing another account's token ID does nothing."""
    return _accounts().revoke_token(username, token_id)


def revoke_all_tokens(username: str) -> int:
    """Every token of the account, API and workstation ("cli") alike: used when an administrator resets the
    password, i.e. when the account may be in the wrong hands. Returns how many were revoked."""
    return _accounts().revoke_all_tokens(username)


def verify_token(token: str):
    """Used by security.py::get_current_user as a fallback when the presented
    token is not a valid JWT. Returns the full user row (same shape as
    security.get_user) or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    token_hash = _hash(token)
    row = _accounts().token_by_hash(token_hash)
    if not row:
        return None
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
        return None
    _accounts().touch_token(token_hash, datetime.now(UTC).isoformat())
    user = _accounts().get(row["username"])
    if not user:
        return None
    # Which token authenticated the request: a token may revoke itself, never create or revoke others.
    return {**user, "api_token_id": row["id"]}
