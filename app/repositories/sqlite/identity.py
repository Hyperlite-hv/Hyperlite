"""Identity providers in SQLite: security keys (`webauthn_credentials`, `webauthn_challenges`), the SSO and LDAP
configurations (`sso_config`, `ldap_config`), the short-lived SSO state (`sso_login_state`, `sso_handoffs`) and the
accounts those providers create in `users`.

Secrets never reach this module in clear text: the SSO client secret and the LDAP bind password arrive sealed, the
SSO state binding and handoff codes as digests. Provisioning reports a conflict instead of raising the caller's
exception, so this layer stays free of the provider logic.
"""

import asyncio

from app.core.database import get_conn

# Outcomes of a provisioning: the account was created or updated, or the name is someone else's.
PROVISIONED = "ok"
LOCAL_ACCOUNT = "local"
OTHER_SUBJECT = "subject"


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


def _upsert_singleton(db, table, fields):
    """Writes the one row (id = 1) of a configuration table. Column names come from the caller's allowlist only;
    the values are bound."""
    if db.execute(f"SELECT id FROM {table} WHERE id = 1").fetchone():  # noqa: S608
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE {table} SET {sets} WHERE id = 1", list(fields.values()))  # noqa: S608
    else:
        cols = ["id", *fields]
        db.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
            [1, *fields.values()],
        )


class SqliteIdentityStore:
    # ---- Security keys ----

    def list_keys(self, username):
        with get_conn() as db:
            rows = db.execute(
                "SELECT id, nom, rp_id, cree_le, utilise_le FROM webauthn_credentials WHERE username = ? ORDER BY cree_le",
                (username,),
            ).fetchall()
        return [dict(r) for r in rows]

    def has_keys(self, username):
        return _one("SELECT 1 AS x FROM webauthn_credentials WHERE username = ? LIMIT 1", (username,)) is not None

    def delete_key(self, username, key_id):
        return _write("DELETE FROM webauthn_credentials WHERE id = ? AND username = ?", (key_id, username)).rowcount > 0

    def delete_all_keys(self, username):
        _write("DELETE FROM webauthn_credentials WHERE username = ?", (username,))

    def credential_ids(self, username, rp_id):
        with get_conn() as db:
            rows = db.execute(
                "SELECT credential_id FROM webauthn_credentials WHERE username = ? AND rp_id = ?", (username, rp_id)
            ).fetchall()
        return [r["credential_id"] for r in rows]

    def credential(self, username, credential_id, rp_id):
        return _one(
            "SELECT id, public_key, sign_count FROM webauthn_credentials WHERE username = ? AND credential_id = ? AND rp_id = ?",
            (username, credential_id, rp_id),
        )

    def add_credential(self, username, name, credential_id, public_key, sign_count, rp_id, created_at):
        """The new key's id, or None when this credential is already registered (to anyone)."""
        with get_conn() as db:
            if db.execute("SELECT 1 FROM webauthn_credentials WHERE credential_id = ?", (credential_id,)).fetchone():
                return None
            cur = db.execute(
                "INSERT INTO webauthn_credentials (username, nom, credential_id, public_key, sign_count, rp_id, cree_le) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, name, credential_id, public_key, sign_count, rp_id, created_at),
            )
            db.commit()
            return cur.lastrowid

    def record_key_use(self, key_id, sign_count, used_at):
        _write(
            "UPDATE webauthn_credentials SET sign_count = ?, utilise_le = ? WHERE id = ?", (sign_count, used_at, key_id)
        )

    def store_challenge(self, username, purpose, challenge, rp_id, origin, expires_at, now):
        with get_conn() as db:
            db.execute("DELETE FROM webauthn_challenges WHERE expire_le < ?", (now,))
            db.execute(
                "INSERT INTO webauthn_challenges (username, but, challenge, rp_id, origin, expire_le) VALUES (?, ?, ?, ?, ?, ?)",
                (username, purpose, challenge, rp_id, origin, expires_at),
            )
            db.commit()

    def take_challenge(self, username, purpose, challenge, rp_id, origin):
        """The challenge's expiry, consumed (single use), or None when it is unknown."""
        with get_conn() as db:
            row = db.execute(
                "SELECT id, expire_le FROM webauthn_challenges WHERE username = ? AND but = ? AND challenge = ? "
                "AND rp_id = ? AND origin = ?",
                (username, purpose, challenge, rp_id, origin),
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM webauthn_challenges WHERE id = ?", (row["id"],))
            db.commit()
        return row["expire_le"]

    # ---- SSO ----

    def sso_config(self):
        return _one("SELECT * FROM sso_config WHERE id = 1")

    def save_sso_config(self, fields):
        with get_conn() as db:
            _upsert_singleton(db, "sso_config", fields)
            db.commit()

    def put_sso_state(self, state, nonce, created_at, binding, older_than):
        with get_conn() as db:
            # Purge expired states along the way: such a short-lived table does not justify a scheduler.
            db.execute("DELETE FROM sso_login_state WHERE created_at < ?", (older_than,))
            db.execute(
                "INSERT INTO sso_login_state (state, nonce, created_at, binding) VALUES (?, ?, ?, ?)",
                (state, nonce, created_at, binding),
            )
            db.commit()

    def take_sso_state(self, state):
        """The state's row, deleted as it is read (single use), or None."""
        with get_conn() as db:
            row = db.execute(
                "SELECT nonce, created_at, binding FROM sso_login_state WHERE state = ?", (state,)
            ).fetchone()
            if not row:
                return None
            db.execute("DELETE FROM sso_login_state WHERE state = ?", (state,))
            db.commit()
        return dict(row)

    def put_handoff(self, code_digest, username, created_at, older_than):
        with get_conn() as db:
            db.execute("DELETE FROM sso_handoffs WHERE created_at < ?", (older_than,))
            db.execute(
                "INSERT INTO sso_handoffs (code, username, created_at) VALUES (?, ?, ?)",
                (code_digest, username, created_at),
            )
            db.commit()

    def take_handoff(self, code_digest):
        with get_conn() as db:
            row = db.execute("SELECT username, created_at FROM sso_handoffs WHERE code = ?", (code_digest,)).fetchone()
            if not row:
                return None
            db.execute("DELETE FROM sso_handoffs WHERE code = ?", (code_digest,))
            db.commit()
        return dict(row)

    def provision_sso_user(self, username, role, subject, new_password_hash):
        """(outcome, users row). The account is found by its IdP subject first; `new_password_hash()` is only
        called for a new account (hashing is deliberately slow)."""
        with get_conn() as db:
            if subject:
                bound = db.execute(
                    "SELECT username FROM users WHERE auth_source = 'sso' AND sso_subject = ?", (subject,)
                ).fetchone()
                if bound is not None:
                    username = bound["username"]
            existing = db.execute(
                "SELECT username, role, auth_source, sso_subject FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing is not None and existing["auth_source"] != "sso":
                return LOCAL_ACCOUNT, {"username": username}
            if existing is not None and subject and existing["sso_subject"] and existing["sso_subject"] != subject:
                return OTHER_SUBJECT, {"username": username}
            if existing is None:
                db.execute(
                    "INSERT INTO users (username, hashed_password, role, auth_source, sso_subject) VALUES (?, ?, ?, 'sso', ?)",
                    (username, new_password_hash(), role, subject),
                )
            else:
                db.execute(
                    "UPDATE users SET role = ?, sso_subject = COALESCE(sso_subject, ?) WHERE username = ?",
                    (role, subject, username),
                )
            db.commit()
            return PROVISIONED, dict(db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())

    # ---- LDAP ----

    def ldap_config(self):
        return _one("SELECT * FROM ldap_config WHERE id = 1")

    def save_ldap_config(self, fields):
        with get_conn() as db:
            _upsert_singleton(db, "ldap_config", fields)
            db.commit()

    def provision_ldap_user(self, username, dn, role, new_password_hash):
        """(outcome, users row); `new_password_hash()` is only called for a new account."""
        with get_conn() as db:
            row = db.execute("SELECT username, auth_source FROM users WHERE username = ?", (username,)).fetchone()
            if row is not None and row["auth_source"] != "ldap":
                return LOCAL_ACCOUNT, {"username": username}
            if row is None:
                db.execute(
                    "INSERT INTO users (username, hashed_password, role, auth_source, ldap_dn) VALUES (?, ?, ?, 'ldap', ?)",
                    (username, new_password_hash(), role, dn),
                )
            else:
                db.execute("UPDATE users SET role = ?, ldap_dn = ? WHERE username = ?", (role, dn, username))
            db.commit()
            return PROVISIONED, dict(db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


class SqliteIdentityRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteIdentityStore()

    async def list_keys(self, username):
        return await asyncio.to_thread(self.sync.list_keys, username)

    async def has_keys(self, username):
        return await asyncio.to_thread(self.sync.has_keys, username)

    async def sso_config(self):
        return await asyncio.to_thread(self.sync.sso_config)

    async def ldap_config(self):
        return await asyncio.to_thread(self.sync.ldap_config)
