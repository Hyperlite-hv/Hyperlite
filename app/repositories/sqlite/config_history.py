"""VM definition versions in SQLite (`vm_config_versions`, app/core/config_history.py): this node's own history."""

from app.core.database import get_conn


class SqliteConfigHistoryStore:
    def last_hash(self, target):
        with get_conn() as db:
            row = db.execute(
                "SELECT hash FROM vm_config_versions WHERE target = ? ORDER BY id DESC LIMIT 1", (target,)
            ).fetchone()
        return row["hash"] if row else None

    def add(self, target, ts, digest, xml, keep):
        with get_conn() as db:
            db.execute(
                "INSERT INTO vm_config_versions (target, ts, hash, xml) VALUES (?, ?, ?, ?)", (target, ts, digest, xml)
            )
            db.execute(
                "DELETE FROM vm_config_versions WHERE target = ? AND id NOT IN "
                "(SELECT id FROM vm_config_versions WHERE target = ? ORDER BY id DESC LIMIT ?)",
                (target, target, keep),
            )
            db.commit()

    def versions(self, target):
        with get_conn() as db:
            rows = db.execute(
                "SELECT id, ts, hash FROM vm_config_versions WHERE target = ? ORDER BY id DESC", (target,)
            ).fetchall()
        return [dict(r) for r in rows]

    def version(self, target, version_id):
        with get_conn() as db:
            row = db.execute(
                "SELECT * FROM vm_config_versions WHERE target = ? AND id = ?", (target, version_id)
            ).fetchone()
        return dict(row) if row else None

    def previous(self, target, version_id):
        with get_conn() as db:
            row = db.execute(
                "SELECT * FROM vm_config_versions WHERE target = ? AND id < ? ORDER BY id DESC LIMIT 1",
                (target, version_id),
            ).fetchone()
        return dict(row) if row else None
