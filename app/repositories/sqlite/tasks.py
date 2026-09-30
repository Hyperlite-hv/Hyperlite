"""Tasks in SQLite: the `tasks` and `task_logs` tables.

The in-memory part of tasks (who can cancel what, which tasks this process runs) stays in app/core/tasks.py: it is
runtime state of one process, not storage. `.sync` is the synchronous bridge for the code that still runs in
threads (every long operation), removed as those callers become asynchronous.
"""

import asyncio

from app.core.database import get_conn
from app.repositories.sqlite.export import newest_first

MAX_LOG_LINES = 500
RUNNING = ("en_cours", "en_attente")
# Columns allowed for sorting: an allowlist, the SQL text never contains request data.
SORT_COLUMNS = {c: c for c in ("cree_le", "debut_le", "fin_le", "statut", "type", "cible", "username")}
EXPORT_COLUMNS = (
    "id",
    "cree_le",
    "type",
    "cible",
    "node",
    "username",
    "statut",
    "progres",
    "debut_le",
    "fin_le",
    "erreur",
)


def _log_in(conn, task_id, message, at):
    count = conn.execute("SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (task_id,)).fetchone()[0]
    if count >= MAX_LOG_LINES:
        return  # a runaway loop must not fill the database; the task's end is still recorded in the task itself
    conn.execute("INSERT INTO task_logs (task_id, at, message) VALUES (?, ?, ?)", (task_id, at, str(message)[:2000]))


def _clauses(filters):
    """filters: statut, type, username, node (exact), cible (part of a name), depuis (cree_le >=), objet (exact
    target), famille ("vm" or "container")."""
    clauses, params = [], []
    for column in ("statut", "type", "username", "node"):
        if filters.get(column):
            clauses.append(f"{column} = ?")
            params.append(filters[column])
    if filters.get("cible"):
        clauses.append("cible LIKE ?")
        params.append(f"%{filters['cible']}%")
    if filters.get("depuis"):
        clauses.append("cree_le >= ?")
        params.append(filters["depuis"])
    if filters.get("objet"):
        clauses.append("cible = ?")
        params.append(filters["objet"])
    if filters.get("famille"):
        # Container task types all name the container (create_container, clone_container, backup_container...).
        clauses.append(
            "type LIKE '%container%'" if filters["famille"] == "container" else "type NOT LIKE '%container%'"
        )
    return clauses, params


class SqliteTaskStore:
    def create(self, task_id, type_, cible, node, username, now):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, type, cible, node, username, statut, progres, cree_le, debut_le) "
                "VALUES (?, ?, ?, ?, ?, 'en_cours', 0, ?, ?)",
                (task_id, type_, cible, node, username, now, now),
            )
            _log_in(conn, task_id, f"Started by {username or 'the system'}", now)
            conn.commit()

    def get(self, task_id):
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def status(self, task_id):
        with get_conn() as conn:
            row = conn.execute("SELECT statut FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["statut"] if row else None

    def list(self, filters, sort="cree_le", order="desc", limit=200):
        clauses, params = _clauses(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sort = SORT_COLUMNS.get(sort, "cree_le")
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

    def update_progress(self, task_id, progres):
        with get_conn() as conn:
            conn.execute("UPDATE tasks SET progres = ? WHERE id = ?", (progres, task_id))
            conn.commit()

    def finish(self, task_id, statut, error_message, cancelled_by, now):
        with get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET statut = ?, progres = 100, fin_le = ?, erreur = ?, annule_par = ? WHERE id = ?",
                (statut, now, error_message, cancelled_by, task_id),
            )
            _log_in(
                conn,
                task_id,
                ("Failed: " + error_message if error_message else "Failed") if statut == "echec" else "Finished",
                now,
            )
            conn.commit()

    def close(self, task_id, username, reason, now):
        """Closed by hand, or because nobody runs it any more: failed, recorded as cancelled by `username`."""
        message = f"Closed by {username}: {reason}"
        with get_conn() as conn:
            _log_in(conn, task_id, message, now)
            conn.execute(
                "UPDATE tasks SET statut = 'echec', progres = 100, fin_le = ?, erreur = ?, annule_par = ? WHERE id = ?",
                (now, message, username, task_id),
            )
            conn.commit()

    def close_interrupted(self, now):
        reason = "Interrupted: the Hyperlite service restarted while it ran"
        with get_conn() as conn:
            rows = conn.execute("SELECT id FROM tasks WHERE statut IN ('en_cours', 'en_attente')").fetchall()
            for row in rows:
                _log_in(conn, row["id"], reason, now)
            conn.execute(
                "UPDATE tasks SET statut = 'echec', progres = 100, fin_le = ?, erreur = ? WHERE statut IN ('en_cours', 'en_attente')",
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
                "SELECT username FROM tasks WHERE type = ? AND statut = 'en_cours' AND cree_le > ? "
                "ORDER BY cree_le DESC LIMIT 1",
                (type_, since),
            ).fetchone()
        return (row["username"] or "") if row else None

    def stats(self):
        with get_conn() as conn:
            running = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE statut = 'en_cours'").fetchone()["n"]
            total = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
            failed = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE statut = 'echec'").fetchone()["n"]
            avg = conn.execute(
                "SELECT AVG((julianday(fin_le) - julianday(debut_le)) * 86400) AS avg_s "
                "FROM tasks WHERE statut = 'termine' AND fin_le IS NOT NULL"
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

    async def list(self, filters, sort="cree_le", order="desc", limit=200):
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
