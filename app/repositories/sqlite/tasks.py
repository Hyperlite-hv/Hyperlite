"""Tasks in SQLite: the `tasks` and `task_logs` tables.

The in-memory part of tasks (who can cancel what, which tasks this process runs) stays in app/core/tasks.py: it is
runtime state of one process, not storage. `.sync` is the synchronous bridge for the code that still runs in
threads (every long operation), removed as those callers become asynchronous.
"""

import asyncio

from app.core.database import get_conn
from app.repositories.sqlite.export import newest_first

MAX_LOG_LINES = 500
RUNNING = ("running", "pending")
# Columns allowed for sorting: an allowlist, the SQL text never contains request data.
SORT_COLUMNS = {c: c for c in ("created_at", "started_at", "finished_at", "status", "type", "target", "username")}
EXPORT_COLUMNS = (
    "id",
    "created_at",
    "type",
    "target",
    "node",
    "username",
    "status",
    "progress",
    "started_at",
    "finished_at",
    "error",
)


def _log_in(conn, task_id, message, at):
    count = conn.execute("SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (task_id,)).fetchone()[0]
    if count >= MAX_LOG_LINES:
        return  # a runaway loop must not fill the database; the task's end is still recorded in the task itself
    conn.execute("INSERT INTO task_logs (task_id, at, message) VALUES (?, ?, ?)", (task_id, at, str(message)[:2000]))


def _clauses(filters):
    """filters: status, type, username, node (exact), target (part of a name), since (created_at >=), object (exact
    target), family ("vm" or "container")."""
    from app.core import self_node

    clauses, params = [], []
    for column in ("status", "type", "username", "node"):
        if filters.get(column):
            clauses.append(f"{column} = ?")
            # The tasks record this node under its name (app/core/tasks.py): "local" asks for it.
            params.append(self_node.to_db(filters[column]) if column == "node" else filters[column])
    if filters.get("target"):
        clauses.append("target LIKE ?")
        params.append(f"%{filters['target']}%")
    if filters.get("since"):
        clauses.append("created_at >= ?")
        params.append(filters["since"])
    if filters.get("object"):
        clauses.append("target = ?")
        params.append(filters["object"])
    if filters.get("family"):
        # Container task types all name the container (create_container, clone_container, backup_container...).
        clauses.append("type LIKE '%container%'" if filters["family"] == "container" else "type NOT LIKE '%container%'")
    return clauses, params


class SqliteTaskStore:
    def create(self, task_id, type_, target, node, username, now):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, type, target, node, username, status, progress, created_at, started_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', 0, ?, ?)",
                (task_id, type_, target, node, username, now, now),
            )
            _log_in(conn, task_id, f"Started by {username or 'the system'}", now)
            conn.commit()

    def get(self, task_id):
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def status(self, task_id):
        with get_conn() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["status"] if row else None

    def list(self, filters, sort="created_at", order="desc", limit=200):
        clauses, params = _clauses(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sort = SORT_COLUMNS.get(sort, "created_at")
        order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY {sort} {order_sql} LIMIT ?",  # noqa: S608 -- allowlisted fragments, bound values
                [*params, limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def export_rows(self, filters):
        clauses, params = _clauses(filters)
        return newest_first("tasks", EXPORT_COLUMNS, clauses, params)

    def update_progress(self, task_id, progress):
        with get_conn() as conn:
            conn.execute("UPDATE tasks SET progress = ? WHERE id = ?", (progress, task_id))
            conn.commit()

    def finish(self, task_id, status, error_message, cancelled_by, now):
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, progress = 100, finished_at = ?, error = ?, cancelled_by = ? WHERE id = ?",
                (status, now, error_message, cancelled_by, task_id),
            )
            _log_in(
                conn,
                task_id,
                ("Failed: " + error_message if error_message else "Failed") if status == "failed" else "Finished",
                now,
            )
            conn.commit()

    def close(self, task_id, username, reason, now):
        """Closed by hand, or because nobody runs it any more: failed, recorded as cancelled by `username`."""
        message = f"Closed by {username}: {reason}"
        with get_conn() as conn:
            _log_in(conn, task_id, message, now)
            conn.execute(
                "UPDATE tasks SET status = 'failed', progress = 100, finished_at = ?, error = ?, cancelled_by = ? WHERE id = ?",
                (now, message, username, task_id),
            )
            conn.commit()

    def close_interrupted(self, now):
        reason = "Interrupted: the Hyperlite service restarted while it ran"
        with get_conn() as conn:
            rows = conn.execute("SELECT id FROM tasks WHERE status IN ('running', 'pending')").fetchall()
            for row in rows:
                _log_in(conn, row["id"], reason, now)
            conn.execute(
                "UPDATE tasks SET status = 'failed', progress = 100, finished_at = ?, error = ? WHERE status IN ('running', 'pending')",
                (now, reason),
            )
            conn.commit()
        return len(rows)

    def append_log(self, task_id, message, now):
        with get_conn() as conn:
            _log_in(conn, task_id, message, now)
            conn.commit()

    def logs(self, task_id):
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT at, message FROM task_logs WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_logs_before(self, limit):
        with get_conn() as conn:
            conn.execute("DELETE FROM task_logs WHERE at < ?", (limit,))
            conn.commit()

    def latest_running(self, type_, since):
        """The username behind the newest running task of this type created after `since`, or None."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT username FROM tasks WHERE type = ? AND status = 'running' AND created_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (type_, since),
            ).fetchone()
        return (row["username"] or "") if row else None

    def stats(self):
        with get_conn() as conn:
            running = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = 'running'").fetchone()["n"]
            total = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            failed = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = 'failed'").fetchone()["n"]
            avg = conn.execute(
                "SELECT AVG((julianday(finished_at) - julianday(started_at)) * 86400) AS avg_s "
                "FROM tasks WHERE status = 'succeeded' AND finished_at IS NOT NULL"
            ).fetchone()["avg_s"]
        return {
            "running": running,
            "total": total,
            "failed": failed,
            "avg_duration_s": round(avg, 2) if avg is not None else 0,
        }


class SqliteTaskRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteTaskStore()

    async def get(self, task_id):
        return await asyncio.to_thread(self.sync.get, task_id)

    async def list(self, filters, sort="created_at", order="desc", limit=200):
        return await asyncio.to_thread(self.sync.list, filters, sort, order, limit)

    def export_rows(self, filters):
        # A generator read by the CSV response in its own worker thread, batch by batch.
        return self.sync.export_rows(filters)

    async def logs(self, task_id):
        return await asyncio.to_thread(self.sync.logs, task_id)

    async def stats(self):
        return await asyncio.to_thread(self.sync.stats)

    async def latest_running(self, type_, since):
        return await asyncio.to_thread(self.sync.latest_running, type_, since)
