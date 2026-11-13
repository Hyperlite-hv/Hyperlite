"""Alert rules in SQLite (`alert_rules`, app/core/alert_rules.py): replicated, their firing state too."""

import json

from app.core.database import get_conn

_FIELDS = (
    "name",
    "enabled",
    "scope",
    "node",
    "vm",
    "metric",
    "operator",
    "threshold",
    "window_min",
    "severity",
    "channels",
)


def _rule(row):
    return {
        **row,
        "enabled": bool(row["enabled"]),
        "channels": json.loads(row["channels"] or "[]"),
        "firing": json.loads(row["firing"] or "[]"),
    }


def _values(rule):
    return tuple(
        json.dumps(rule[f]) if f == "channels" else (1 if rule[f] else 0) if f == "enabled" else rule[f]
        for f in _FIELDS
    )


class SqliteAlertRulesStore:
    def rules(self):
        with get_conn() as db:
            return [_rule(dict(r)) for r in db.execute("SELECT * FROM alert_rules ORDER BY name, id").fetchall()]

    def rule(self, rule_id):
        with get_conn() as db:
            row = db.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
        return _rule(dict(row)) if row else None

    def create(self, rule_id, rule, username, now):
        with get_conn() as db:
            db.execute(
                f"INSERT INTO alert_rules (id, {', '.join(_FIELDS)}, created_by, created_at) "  # noqa: S608 -- fixed names
                f"VALUES (?, {', '.join('?' for _ in _FIELDS)}, ?, ?)",
                (rule_id, *_values(rule), username, now),
            )
            db.commit()

    def update(self, rule_id, rule):
        """A changed rule starts over: it fires for nothing until its new condition holds."""
        with get_conn() as db:
            db.execute(
                f"UPDATE alert_rules SET {', '.join(f'{f} = ?' for f in _FIELDS)}, firing = '[]', changed_at = NULL "  # noqa: S608
                "WHERE id = ?",
                (*_values(rule), rule_id),
            )
            db.commit()

    def set_enabled(self, rule_id, enabled):
        with get_conn() as db:
            db.execute(
                "UPDATE alert_rules SET enabled = ?, firing = CASE WHEN ? THEN firing ELSE '[]' END WHERE id = ?",
                (1 if enabled else 0, 1 if enabled else 0, rule_id),
            )
            db.commit()

    def set_firing(self, rule_id, targets, now):
        with get_conn() as db:
            db.execute(
                "UPDATE alert_rules SET firing = ?, changed_at = ? WHERE id = ?", (json.dumps(targets), now, rule_id)
            )
            db.commit()

    def delete(self, rule_id):
        with get_conn() as db:
            cur = db.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
            db.commit()
        return cur.rowcount > 0
