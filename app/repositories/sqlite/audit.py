"""The audit log in SQLite: the `audit_log` table. Written by one thread only (app/core/audit.py)."""

import asyncio

from app.core.database import get_conn
from app.repositories.sqlite.export import newest_first

COLUMNS = "id, timestamp, username, action, resource, result, error_message, ip"
EXPORT_COLUMNS = ("timestamp", "username", "action", "resource", "result", "error_message", "ip")


def _clauses(filters):
    """filters: action, result, username (exact), resource (part of it), depuis / jusqu_a (timestamp bounds)."""
    clauses, params = [], []
    for column in ("action", "result", "username"):
        if filters.get(column):
            clauses.append(f"{column} = ?")
            params.append(filters[column])
    if filters.get("resource"):
        clauses.append("resource LIKE ?")
        params.append(f"%{filters['resource']}%")
    if filters.get("depuis"):
        clauses.append("timestamp >= ?")
        params.append(filters["depuis"])
    if filters.get("jusqu_a"):
        clauses.append("timestamp <= ?")
        params.append(filters["jusqu_a"])
    return clauses, params


def _where(filters):
    clauses, params = _clauses(filters)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


class SqliteAuditStore:
    def append(self, ts, username, action, resource, result, error_message, ip):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, username, action, resource, result, error_message, ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, username, action, resource, result, error_message, ip),
            )
            conn.commit()

    def purge_before(self, limit):
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (limit,))
            conn.commit()
        return cur.rowcount

    def query(self, filters, limit):
        where, params = _where(filters)
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM audit_log {where} ORDER BY id DESC LIMIT ?",  # noqa: S608 -- fixed fragments, bound values
                [*params, limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self, filters):
        where, params = _where(filters)
        with get_conn() as conn:
            return conn.execute(f"SELECT COUNT(*) AS n FROM audit_log {where}", params).fetchone()["n"]  # noqa: S608 -- fixed fragments

    def actions(self):
        with get_conn() as conn:
            return [r["action"] for r in conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]

    def for_resource(self, resource, limit=20):
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT timestamp, action, result, error_message FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT ?",
                (resource, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_rows(self, filters):
        clauses, params = _clauses(filters)
        return newest_first("audit_log", EXPORT_COLUMNS, clauses, params, key="id")


class SqliteAuditRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteAuditStore()

    async def query(self, filters, limit):
        return await asyncio.to_thread(self.sync.query, filters, limit)

    async def count(self, filters):
        return await asyncio.to_thread(self.sync.count, filters)

    async def actions(self):
        return await asyncio.to_thread(self.sync.actions)

    async def for_resource(self, resource, limit=20):
        return await asyncio.to_thread(self.sync.for_resource, resource, limit)

    def export_rows(self, filters):
        return self.sync.export_rows(filters)
