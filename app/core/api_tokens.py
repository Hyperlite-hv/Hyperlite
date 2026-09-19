"""API tokens: an authentication mechanism distinct from session JWTs,
designed for automation (scripts, Terraform, cron). They do not expire
quickly and can be revoked individually without affecting the web session.
Like a password, the plain token is NEVER stored, only its SHA-256 hash: it
cannot be recovered after creation and is shown only once in the UI."""

import hashlib
import secrets
from datetime import UTC, datetime

from app.core.database import get_conn

TOKEN_PREFIX = "hlt_"  # noqa: S105 -- public token prefix, not a credential


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(username: str, name: str):
    """Return (id, plain_token). The plain token cannot be recovered once this
    function has returned."""
    token = generate_token()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_tokens (username, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, name, _hash(token), datetime.now(UTC).isoformat()),
        )
        conn.commit()
        token_id = cur.lastrowid
    return token_id, token


def list_tokens(username: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, last_used_at FROM api_tokens WHERE username = ? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_token(username: str, token_id: int) -> bool:
    """Scoped to a username: a user can only revoke their own tokens, and
    guessing another account's token ID does nothing."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM api_tokens WHERE id = ? AND username = ?", (token_id, username))
        conn.commit()
    return cur.rowcount > 0


def verify_token(token: str):
    """Used by security.py::get_current_user as a fallback when the presented
    token is not a valid JWT. Returns the full user row (same shape as
    security.get_user) or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    token_hash = _hash(token)
    with get_conn() as conn:
        row = conn.execute("SELECT username FROM api_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (datetime.now(UTC).isoformat(), token_hash),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (row["username"],)).fetchone()
    return dict(user) if user else None
