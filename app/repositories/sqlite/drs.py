"""Load balancing in SQLite (docs/design/drs.md): its settings (`drs_settings`), its rules (`drs_rules`), each VM's
exception (`drs_vms`) and the moves of automatic mode (`drs_requests`). All four are replicated: every node reads the
same."""

import json

from app.core.database import get_conn


def _rule(row):
    return {
        **row,
        "vms": json.loads(row["vms"] or "[]"),
        "nodes": json.loads(row["nodes"] or "[]"),
        "enabled": bool(row["enabled"]),
    }


class SqliteDrsStore:
    def settings(self):
        with get_conn() as db:
            return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM drs_settings").fetchall()}

    def save_settings(self, values):
        with get_conn() as db:
            for key, value in values.items():
                db.execute(
                    "INSERT INTO drs_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
            db.commit()

    def rules(self):
        with get_conn() as db:
            return [_rule(dict(r)) for r in db.execute("SELECT * FROM drs_rules ORDER BY name").fetchall()]

    def rule(self, name):
        with get_conn() as db:
            row = db.execute("SELECT * FROM drs_rules WHERE name = ?", (name,)).fetchone()
        return _rule(dict(row)) if row else None

    def save_rule(self, rule, username, now):
        with get_conn() as db:
            db.execute(
                "INSERT INTO drs_rules (name, kind, strength, vms, nodes, enabled, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET kind = excluded.kind, "
                "strength = excluded.strength, vms = excluded.vms, nodes = excluded.nodes, enabled = excluded.enabled",
                (
                    rule["name"],
                    rule["kind"],
                    rule["strength"],
                    json.dumps(rule["vms"]),
                    json.dumps(rule["nodes"]),
                    1 if rule["enabled"] else 0,
                    username,
                    now,
                ),
            )
            db.commit()

    def delete_rule(self, name):
        with get_conn() as db:
            cur = db.execute("DELETE FROM drs_rules WHERE name = ?", (name,))
            db.commit()
        return cur.rowcount > 0

    def behaviours(self):
        with get_conn() as db:
            return {r["vm_name"]: r["behaviour"] for r in db.execute("SELECT * FROM drs_vms").fetchall()}

    def set_behaviour(self, vm_name, behaviour):
        with get_conn() as db:
            if behaviour == "default":
                db.execute("DELETE FROM drs_vms WHERE vm_name = ?", (vm_name,))
            else:
                db.execute(
                    "INSERT INTO drs_vms (vm_name, behaviour) VALUES (?, ?) "
                    "ON CONFLICT(vm_name) DO UPDATE SET behaviour = excluded.behaviour",
                    (vm_name, behaviour),
                )
            db.commit()

    def add_request(self, row):
        with get_conn() as db:
            db.execute(
                "INSERT INTO drs_requests (id, vm_name, source, target, reason, requested_by, requested_at, state, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    row["id"],
                    row["vm_name"],
                    row["source"],
                    row["target"],
                    row["reason"],
                    row["requested_by"],
                    row["requested_at"],
                    row["requested_at"],
                ),
            )
            db.commit()

    def requests(self, source=None):
        with get_conn() as db:
            if source is None:
                rows = db.execute("SELECT * FROM drs_requests ORDER BY requested_at").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM drs_requests WHERE source = ? ORDER BY requested_at", (source,)
                ).fetchall()
        return [dict(r) for r in rows]

    def request(self, request_id):
        with get_conn() as db:
            row = db.execute("SELECT * FROM drs_requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def update_request(self, request_id, state, now, task_id=None, detail=None):
        with get_conn() as db:
            db.execute(
                "UPDATE drs_requests SET state = ?, task_id = COALESCE(?, task_id), detail = COALESCE(?, detail), "
                "updated_at = ? WHERE id = ?",
                (state, task_id, detail, now, request_id),
            )
            db.commit()

    def delete_requests_before(self, when):
        """The finished moves last changed before `when` (ISO time)."""
        with get_conn() as db:
            db.execute("DELETE FROM drs_requests WHERE state IN ('succeeded', 'failed') AND updated_at < ?", (when,))
            db.commit()
