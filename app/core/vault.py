"""The secrets vault (docs/design/governance.md, section 8): passwords, keys and tokens kept sealed with the node's
key (app/core/secrets_crypto.py), read by the accounts given access, and named instead of copied: a VM's password
written `vault:<name>` at creation is read from the vault.

Administrators create, change and remove secrets and say who reads them; reading one is journaled. The value is never
listed, only revealed one at a time.
"""

import uuid
from datetime import UTC, datetime

from app.core import secrets_crypto
from app.core.database import get_conn

PREFIX = "vault:"


class VaultError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC).isoformat()


def secrets():
    with get_conn() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT id, name, description, created_by, created_at, updated_at FROM vault_secrets ORDER BY name"
            )
        ]
        grants = [dict(r) for r in db.execute("SELECT * FROM vault_grants")]
    for r in rows:
        r["readers"] = [g for g in grants if g["secret_id"] == r["id"]]
    return rows


def _by_name(name):
    with get_conn() as db:
        row = db.execute("SELECT * FROM vault_secrets WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def put(name, value, description, username, secret_id=None):
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise VaultError(422, "A secret is named with letters, digits, '.', '-' and '_' (64 at most)")
    with get_conn() as db:
        if secret_id:
            fields = "name = ?, description = ?, updated_at = ?" + (", value = ?" if value else "")
            params = [name, description, _now()] + ([secrets_crypto.encrypt(value)] if value else [])
            cur = db.execute(f"UPDATE vault_secrets SET {fields} WHERE id = ?", (*params, secret_id))  # noqa: S608
            if cur.rowcount == 0:
                raise VaultError(404, "Not found")
        else:
            if not value:
                raise VaultError(422, "A secret needs a value")
            if _by_name(name):
                raise VaultError(409, f"A secret named '{name}' already exists")
            secret_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO vault_secrets (id, name, description, value, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (secret_id, name, description, secrets_crypto.encrypt(value), username, _now(), _now()),
            )
        db.commit()
    return next(s for s in secrets() if s["id"] == secret_id)


def delete(secret_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM vault_secrets WHERE id = ?", (secret_id,))
        db.execute("DELETE FROM vault_grants WHERE secret_id = ?", (secret_id,))
        db.commit()
    return cur.rowcount > 0


def set_readers(secret_id, readers):
    """`readers`: [(subject_type, subject_id)]."""
    with get_conn() as db:
        db.execute("DELETE FROM vault_grants WHERE secret_id = ?", (secret_id,))
        for kind, ident in readers:
            db.execute(
                "INSERT INTO vault_grants (id, secret_id, subject_type, subject_id) VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, secret_id, kind, str(ident)),
            )
        db.commit()


def may_read(user, secret):
    if user.get("token_scope"):
        # A delegated token acts with one role on one perimeter: the vault's secrets are not in it, whatever its owner
        # may read.
        return False
    if user["role"] == "admin":
        return True
    from app.core import permissions

    groups = set(permissions.get_user_groups(user["username"]))
    return any(permissions._subject_matches(g, user["username"], groups) for g in secret["readers"])


def reveal(user, name):
    """The value of the secret named `name`, for an account that may read it; journaled."""
    from app.core.audit import log_action

    found = _by_name(name)
    listed = next((s for s in secrets() if s["name"] == name), None)
    if found is None or listed is None:
        raise VaultError(404, f"No secret named '{name}'")
    if not may_read(user, listed):
        raise VaultError(403, f"You may not read the secret '{name}'")
    log_action(user["username"], "vault_read", name, "success")
    return secrets_crypto.decrypt(found["value"])


def resolve(user, value):
    """`value` itself, or the secret it names when written `vault:<name>`."""
    if isinstance(value, str) and value.startswith(PREFIX):
        return reveal(user, value[len(PREFIX) :])
    return value


def forget_account(username):
    """A deleted account's read grants: never handed to the next account created under that name."""
    with get_conn() as db:
        db.execute("DELETE FROM vault_grants WHERE subject_type = 'user' AND subject_id = ?", (username,))
        db.commit()
