"""VM schedules in SQLite (`vm_schedules`, app/core/vm_schedules.py): replicated, their outcome too."""

import json

from app.core.database import get_conn


def _row(row):
    return {
        **row,
        "days": json.loads(row["days"] or "[]"),
        "made": json.loads(row["made"] or "[]"),
        "enabled": bool(row["enabled"]),
    }


class SqliteVmSchedulesStore:
    def schedules(self, vm_name=None):
        with get_conn() as db:
            if vm_name is None:
                rows = db.execute("SELECT * FROM vm_schedules ORDER BY vm_name, time").fetchall()
            else:
                rows = db.execute("SELECT * FROM vm_schedules WHERE vm_name = ? ORDER BY time", (vm_name,)).fetchall()
        return [_row(dict(r)) for r in rows]

    def schedule(self, schedule_id):
        with get_conn() as db:
            row = db.execute("SELECT * FROM vm_schedules WHERE id = ?", (schedule_id,)).fetchone()
        return _row(dict(row)) if row else None

    def due(self, now):
        with get_conn() as db:
            rows = db.execute(
                "SELECT * FROM vm_schedules WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run",
                (now,),
            ).fetchall()
        return [_row(dict(r)) for r in rows]

    def create(self, schedule_id, s, next_run, username, now):
        with get_conn() as db:
            db.execute(
                "INSERT INTO vm_schedules (id, vm_name, action, days, time, keep, enabled, next_run, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    schedule_id,
                    s["vm_name"],
                    s["action"],
                    json.dumps(s["days"]),
                    s["time"],
                    s["keep"],
                    1 if s["enabled"] else 0,
                    next_run,
                    username,
                    now,
                ),
            )
            db.commit()

    def update(self, schedule_id, s, next_run):
        with get_conn() as db:
            db.execute(
                "UPDATE vm_schedules SET action = ?, days = ?, time = ?, keep = ?, enabled = ?, next_run = ? WHERE id = ?",
                (
                    s["action"],
                    json.dumps(s["days"]),
                    s["time"],
                    s["keep"],
                    1 if s["enabled"] else 0,
                    next_run,
                    schedule_id,
                ),
            )
            db.commit()

    def record(self, schedule_id, last_run, last_result, made, next_run):
        with get_conn() as db:
            db.execute(
                "UPDATE vm_schedules SET last_run = ?, last_result = ?, made = ?, next_run = ? WHERE id = ?",
                (last_run, last_result, json.dumps(made), next_run, schedule_id),
            )
            db.commit()

    def delete(self, schedule_id):
        with get_conn() as db:
            cur = db.execute("DELETE FROM vm_schedules WHERE id = ?", (schedule_id,))
            db.commit()
        return cur.rowcount > 0
