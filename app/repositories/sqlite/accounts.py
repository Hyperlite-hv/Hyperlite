"""Accounts in SQLite: `users`, `api_tokens`, `revoked_sessions` and `login_failures`.

Everything that signs a user in runs synchronously today (FastAPI dependencies, the sign-in form): those callers use
`.sync`. Secrets never reach this module in clear text: passwords arrive hashed, TOTP secrets sealed, API tokens as
their SHA-256.
"""

import asyncio
import sqlite3

from app.core.database import get_conn
from app.domain.common import AlreadyExists


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


class SqliteAccountStore:
    # ---- Users ----

    def get(self, username):
        return _one("SELECT * FROM users WHERE username = ?", (username,))

    def role_of(self, username):
        row = _one("SELECT role FROM users WHERE username = ?", (username,))
        return row["role"] if row else None

    def list_summary(self):
        with get_conn() as db:
            rows = db.execute(
                "SELECT username, role, auth_source, totp_enabled, last_login_at FROM users ORDER BY username"
            ).fetchall()
        return [{**dict(r), "totp_enabled": bool(r["totp_enabled"])} for r in rows]

    def create(self, username, hashed_password, role, auth_source=None, ldap_dn=None):
        try:
            with get_conn() as db:
                if auth_source is None:
                    db.execute(
                        "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                        (username, hashed_password, role),
                    )
                else:
                    db.execute(
                        "INSERT INTO users (username, hashed_password, role, auth_source, ldap_dn) VALUES (?, ?, ?, ?, ?)",
                        (username, hashed_password, role, auth_source, ldap_dn),
                    )
                db.commit()
        except sqlite3.IntegrityError as e:
            raise AlreadyExists(f"User '{username}' already exists") from e

    def update(self, username, role=None, hashed_password=None, changed_at=None):
        """Role and password in one transaction. A new password clears must_change_password and records when it
        changed, which signs out every session issued before (app/core/security.py)."""
        with get_conn() as db:
            if role is not None:
                db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
            if hashed_password is not None:
                db.execute(
                    "UPDATE users SET hashed_password = ?, password_changed_at = ?, must_change_password = 0 WHERE username = ?",
                    (hashed_password, changed_at, username),
                )
            db.commit()

    def set_ldap(self, username, role, dn):
        _write("UPDATE users SET role = ?, ldap_dn = ? WHERE username = ?", (role, dn, username))

    def delete(self, username):
        _write("DELETE FROM users WHERE username = ?", (username,))

    def other_admins(self, username):
        with get_conn() as db:
            return db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND username != ?", (username,)
            ).fetchone()["n"]

    def record_login(self, username, at):
        _write("UPDATE users SET last_login_at = ? WHERE username = ?", (at, username))

    def flag_must_change(self, username):
        _write("UPDATE users SET must_change_password = 1 WHERE username = ?", (username,))

    # ---- TOTP (secrets arrive sealed) ----

    def set_totp_secret(self, username, sealed):
        _write("UPDATE users SET totp_secret = ?, totp_last_step = NULL WHERE username = ?", (sealed, username))

    def enable_totp(self, username):
        _write("UPDATE users SET totp_enabled = 1 WHERE username = ?", (username,))

    def disable_totp(self, username):
        _write("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?", (username,))

    def claim_totp_step(self, username, step):
        """One conditional write: of two concurrent uses of the same code, only one updates the row."""
        cur = _write(
            "UPDATE users SET totp_last_step = ? WHERE username = ? AND (totp_last_step IS NULL OR totp_last_step < ?)",
            (step, username, step),
        )
        return cur.rowcount == 1

    def totp_secrets(self):
        with get_conn() as db:
            return [
                dict(r) for r in db.execute("SELECT username, totp_secret FROM users WHERE totp_secret IS NOT NULL")
            ]

    def replace_totp_secret(self, username, sealed):
        _write("UPDATE users SET totp_secret = ? WHERE username = ?", (sealed, username))

    # ---- API tokens (stored as their hash) ----

    def create_token(self, username, name, token_hash, created_at, expires_at, kind):
        return _write(
            "INSERT INTO api_tokens (username, name, token_hash, created_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, ?)",
            (username, name, token_hash, created_at, expires_at, kind),
        ).lastrowid

    def list_tokens(self, username):
        with get_conn() as db:
            rows = db.execute(
                "SELECT id, name, created_at, last_used_at, expires_at, kind FROM api_tokens WHERE username = ? ORDER BY created_at DESC",
                (username,),
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_token(self, username, token_id):
        return _write("DELETE FROM api_tokens WHERE id = ? AND username = ?", (token_id, username)).rowcount > 0

    def revoke_all_tokens(self, username):
        return _write("DELETE FROM api_tokens WHERE username = ?", (username,)).rowcount

    def token_by_hash(self, token_hash):
        return _one("SELECT id, username, expires_at FROM api_tokens WHERE token_hash = ?", (token_hash,))

    def touch_token(self, token_hash, at):
        _write("UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?", (at, token_hash))

    # ---- Signed-out sessions ----

    def revoke_session(self, jti, expires_at, now):
        with get_conn() as db:
            db.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT OR IGNORE INTO revoked_sessions (jti, expires_at) VALUES (?, ?)", (jti, expires_at))
            db.commit()

    def session_revoked(self, jti):
        return _one("SELECT 1 AS x FROM revoked_sessions WHERE jti = ?", (jti,)) is not None

    # ---- Failed sign-ins (the brute-force lock) ----

    def failures_since(self, kind, key, since):
        with get_conn() as db:
            return db.execute(
                "SELECT COUNT(*) FROM login_failures WHERE kind = ? AND key = ? AND at > ?", (kind, key, since)
            ).fetchone()[0]

    def record_failure(self, kind, key, now, keep_after):
        with get_conn() as db:
            db.execute("INSERT INTO login_failures (kind, key, at) VALUES (?, ?, ?)", (kind, key, now))
            db.execute("DELETE FROM login_failures WHERE at < ?", (keep_after,))
            db.commit()

    def clear_failures(self, kind, key):
        _write("DELETE FROM login_failures WHERE kind = ? AND key = ?", (kind, key))


class SqliteAccountRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteAccountStore()

    async def get(self, username):
        return await asyncio.to_thread(self.sync.get, username)

    async def list_summary(self):
        return await asyncio.to_thread(self.sync.list_summary)

    async def list_tokens(self, username):
        return await asyncio.to_thread(self.sync.list_tokens, username)
