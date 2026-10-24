"""Load balancing in SQLite (docs/design/drs.md): its settings (`drs_settings`), its rules (`drs_rules`) and each VM's
exception (`drs_vms`). All three are replicated: every node reads the same."""

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
