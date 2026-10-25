"""The remote clusters of the multi-cluster view in SQLite (app/core/sites.py, docs/design/multi-cluster.md): `remotes`,
their `remote_endpoints` (an address and its pinned certificate per node) and their `remote_tokens` (a token per use,
sealed by app/core/secrets_crypto.py, replicated under /priv). The view and its callers run in threads: `.sync`."""

from app.core.database import get_conn


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _rows(sql, params=()):
    with get_conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


class SqliteSiteStore:
    def list_remotes(self):
        return _rows("SELECT * FROM remotes ORDER BY name")

    def get_remote(self, name):
        return _one("SELECT * FROM remotes WHERE name = ?", (name,))

    def endpoints(self, remote):
        return _rows("SELECT * FROM remote_endpoints WHERE remote = ? ORDER BY position", (remote,))

    def save_remote(self, name, created_by, created_at, endpoints):
        """The remote and its endpoints, in one transaction, in the order they are tried."""
        with get_conn() as db:
            db.execute(
                "INSERT INTO remotes (name, created_by, created_at) VALUES (?, ?, ?)", (name, created_by, created_at)
            )
            db.executemany(
                "INSERT INTO remote_endpoints (remote, position, address, port, fingerprint, node) VALUES (?, ?, ?, ?, ?, ?)",
                [(name, i, e["address"], e["port"], e["fingerprint"], e.get("node")) for i, e in enumerate(endpoints)],
            )
            db.commit()

    def delete_remote(self, name):
        with get_conn() as db:
            db.execute("DELETE FROM remote_tokens WHERE remote = ?", (name,))
            db.execute("DELETE FROM remote_endpoints WHERE remote = ?", (name,))
            db.execute("DELETE FROM remotes WHERE name = ?", (name,))
            db.commit()

    def save_token(self, remote, use, sealed):
        with get_conn() as db:
            db.execute(
                "INSERT OR REPLACE INTO remote_tokens (remote, use, token_encrypted) VALUES (?, ?, ?)",
                (remote, use, sealed),
            )
            db.commit()

    def delete_token(self, remote, use):
        with get_conn() as db:
            db.execute("DELETE FROM remote_tokens WHERE remote = ? AND use = ?", (remote, use))
            db.commit()

    def get_token(self, remote, use):
        row = _one("SELECT token_encrypted FROM remote_tokens WHERE remote = ? AND use = ?", (remote, use))
        return row["token_encrypted"] if row else None

    def token_uses(self, remote):
        return {r["use"] for r in _rows("SELECT use FROM remote_tokens WHERE remote = ?", (remote,))}


class SqliteSiteRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteSiteStore()
