"""Metrics in SQLite: `metrics_samples`, `storage_samples` (time series, bound for Prometheus / VictoriaMetrics in
phase 9) and `metric_servers` (where samples are exported). The collector runs in a thread: it uses `.sync`."""

import asyncio

from app.core.database import get_conn

SAMPLE_FIELDS = (
    "cpu_pct",
    "mem_used_mb",
    "mem_total_mb",
    "disk_read_bps",
    "disk_write_bps",
    "net_rx_bps",
    "net_tx_bps",
)
SERVER_COLUMNS = ("nom", "type", "url", "hote", "port", "org", "bucket", "prefixe", "actif")


class SqliteMetricsStore:
    # ---- Samples ----

    def write_samples(self, ts, rows, pool_rows):
        """rows: (scope, cible, *SAMPLE_FIELDS); pool_rows: (node, pool, capacity_b, allocation_b)."""
        with get_conn() as db:
            db.executemany(
                "INSERT INTO metrics_samples (ts, tier, scope, cible, cpu_pct, mem_used_mb, mem_total_mb, "
                "disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps) VALUES (?, 'raw', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ts, scope, cible, *vals) for scope, cible, *vals in rows],
            )
            db.executemany(
                "INSERT INTO storage_samples (ts, tier, node, pool, capacity_b, allocation_b) VALUES (?, 'raw', ?, ?, ?, ?)",
                [(ts, *p) for p in pool_rows],
            )
            db.commit()

    def rollup_and_prune(self, now_iso, hour_ago, raw_cutoff, hourly_cutoff):
        """Condense the raw samples of the elapsed hour into one average per target ('hourly' tier), then purge
        raw samples older than raw_cutoff and hourly ones older than hourly_cutoff."""
        with get_conn() as db:
            cibles = db.execute(
                "SELECT DISTINCT cible, scope FROM metrics_samples WHERE tier='raw' AND ts >= ?", (hour_ago,)
            ).fetchall()
            for row in cibles:
                avg = db.execute(
                    "SELECT AVG(cpu_pct), AVG(mem_used_mb), AVG(mem_total_mb), AVG(disk_read_bps), "
                    "AVG(disk_write_bps), AVG(net_rx_bps), AVG(net_tx_bps) "
                    "FROM metrics_samples WHERE tier='raw' AND cible=? AND ts >= ?",
                    (row["cible"], hour_ago),
                ).fetchone()
                db.execute(
                    "INSERT INTO metrics_samples (ts, tier, scope, cible, cpu_pct, mem_used_mb, mem_total_mb, "
                    "disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps) VALUES (?, 'hourly', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now_iso, row["scope"], row["cible"], *avg),
                )
            pools = db.execute(
                "SELECT DISTINCT node, pool FROM storage_samples WHERE tier='raw' AND ts >= ?", (hour_ago,)
            ).fetchall()
            for row in pools:
                avg = db.execute(
                    "SELECT AVG(capacity_b), AVG(allocation_b) FROM storage_samples "
                    "WHERE tier='raw' AND node=? AND pool=? AND ts >= ?",
                    (row["node"], row["pool"], hour_ago),
                ).fetchone()
                db.execute(
                    "INSERT INTO storage_samples (ts, tier, node, pool, capacity_b, allocation_b) VALUES (?, 'hourly', ?, ?, ?, ?)",
                    (now_iso, row["node"], row["pool"], *avg),
                )
            db.execute("DELETE FROM storage_samples WHERE tier='raw' AND ts < ?", (raw_cutoff,))
            db.execute("DELETE FROM storage_samples WHERE tier='hourly' AND ts < ?", (hourly_cutoff,))
            db.execute("DELETE FROM metrics_samples WHERE tier='raw' AND ts < ?", (raw_cutoff,))
            db.execute("DELETE FROM metrics_samples WHERE tier='hourly' AND ts < ?", (hourly_cutoff,))
            db.commit()

    def history(self, cible, tier, since):
        with get_conn() as db:
            rows = db.execute(
                "SELECT ts, cpu_pct, mem_used_mb, mem_total_mb, disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps "
                "FROM metrics_samples WHERE cible = ? AND tier = ? AND ts >= ? ORDER BY ts ASC",
                (cible, tier, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def storage_history(self, tier, since, node=None):
        clauses, params = ["tier = ?", "ts >= ?"], [tier, since]
        if node:
            clauses.append("node = ?")
            params.append(node)
        with get_conn() as db:
            rows = db.execute(
                f"SELECT ts, node, pool, capacity_b, allocation_b FROM storage_samples WHERE {' AND '.join(clauses)} "  # noqa: S608 -- fixed fragments only
                "ORDER BY node, pool, ts ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_by_cible(self):
        with get_conn() as db:
            rows = db.execute(
                "SELECT m.* FROM metrics_samples m "
                "INNER JOIN (SELECT cible, MAX(ts) AS max_ts FROM metrics_samples WHERE tier='raw' GROUP BY cible) latest "
                "ON m.cible = latest.cible AND m.ts = latest.max_ts WHERE m.tier='raw'"
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_tick(self):
        """(scope, cible, *SAMPLE_FIELDS) of the most recent collector tick."""
        with get_conn() as db:
            rows = db.execute(
                f"SELECT scope, cible, {', '.join(SAMPLE_FIELDS)} FROM metrics_samples WHERE tier = 'raw' AND ts = "  # noqa: S608 -- fixed fields
                "(SELECT MAX(ts) FROM metrics_samples WHERE tier = 'raw')"
            ).fetchall()
        return [tuple(r) for r in rows]

    # ---- Export servers ----

    def list_servers(self, active_only=False):
        with get_conn() as db:
            if active_only:
                rows = db.execute("SELECT * FROM metric_servers WHERE actif = 1 ORDER BY nom").fetchall()
            else:
                rows = db.execute("SELECT * FROM metric_servers ORDER BY nom").fetchall()
        return [dict(r) for r in rows]

    def get_server(self, server_id):
        with get_conn() as db:
            row = db.execute("SELECT * FROM metric_servers WHERE id = ?", (server_id,)).fetchone()
        return dict(row) if row else None

    def name_taken(self, name, server_id=None):
        with get_conn() as db:
            return bool(
                db.execute("SELECT 1 FROM metric_servers WHERE nom = ? AND id IS NOT ?", (name, server_id)).fetchone()
            )

    def save_server(self, values, encrypted_token, server_id=None):
        """values: SERVER_COLUMNS in order. encrypted_token: None keeps the stored one. Returns the id."""
        with get_conn() as db:
            if server_id is None:
                cur = db.execute(
                    f"INSERT INTO metric_servers ({', '.join(SERVER_COLUMNS)}, jeton) VALUES ({', '.join('?' * (len(SERVER_COLUMNS) + 1))})",  # noqa: S608
                    [*values, encrypted_token],
                )
                server_id = cur.lastrowid
            else:
                db.execute(
                    f"UPDATE metric_servers SET {', '.join(f'{c} = ?' for c in SERVER_COLUMNS)} WHERE id = ?",  # noqa: S608
                    [*values, server_id],
                )
                if encrypted_token:
                    db.execute("UPDATE metric_servers SET jeton = ? WHERE id = ?", (encrypted_token, server_id))
            db.commit()
        return server_id

    def delete_server(self, server_id):
        with get_conn() as db:
            cur = db.execute("DELETE FROM metric_servers WHERE id = ?", (server_id,))
            db.commit()
        return cur.rowcount > 0

    def record_send(self, server_id, sent_at=None, error=None):
        with get_conn() as db:
            if error is None:
                db.execute(
                    "UPDATE metric_servers SET dernier_envoi = ?, derniere_erreur = NULL WHERE id = ?",
                    (sent_at, server_id),
                )
            else:
                db.execute("UPDATE metric_servers SET derniere_erreur = ? WHERE id = ?", (error, server_id))
            db.commit()


class SqliteMetricsRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteMetricsStore()

    async def history(self, cible, tier, since):
        return await asyncio.to_thread(self.sync.history, cible, tier, since)

    async def storage_history(self, tier, since, node=None):
        return await asyncio.to_thread(self.sync.storage_history, tier, since, node)

    async def latest_by_cible(self):
        return await asyncio.to_thread(self.sync.latest_by_cible)

    async def latest_tick(self):
        return await asyncio.to_thread(self.sync.latest_tick)
