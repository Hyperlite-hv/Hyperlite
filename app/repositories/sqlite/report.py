"""What the weekly report reads (app/core/weekly_report.py): backups, journal entries and samples since a date."""

from app.core.database import get_conn


class SqliteReportStore:
    def backups_since(self, since):
        with get_conn() as db:
            rows = db.execute(
                "SELECT vm_name, status, size_bytes, error FROM backups WHERE created_at >= ?", (since,)
            ).fetchall()
        return [dict(r) for r in rows]

    def journal_since(self, since):
        """(action, result, resource) of every entry since `since`, the system's included."""
        with get_conn() as db:
            rows = db.execute(
                "SELECT action, result, resource FROM audit_log WHERE timestamp >= ?", (since,)
            ).fetchall()
        return [dict(r) for r in rows]

    def load_since(self, since):
        """Per target: the average and the peak of the processor, the peak of the memory, from the hourly samples."""
        with get_conn() as db:
            rows = db.execute(
                "SELECT target, scope, AVG(cpu_pct) AS cpu_avg, MAX(cpu_pct) AS cpu_max, "
                "MAX(CASE WHEN mem_total_mb > 0 THEN mem_used_mb * 100.0 / mem_total_mb END) AS mem_max "
                "FROM metrics_samples WHERE tier = 'hourly' AND ts >= ? GROUP BY target, scope",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def pools_latest(self, since):
        """The last sample of each pool since `since`."""
        with get_conn() as db:
            rows = db.execute(
                "SELECT s.node, s.pool, s.capacity_b, s.allocation_b FROM storage_samples s "
                "JOIN (SELECT node, pool, MAX(ts) AS ts FROM storage_samples WHERE ts >= ? GROUP BY node, pool) l "
                "ON s.node = l.node AND s.pool = l.pool AND s.ts = l.ts",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]
