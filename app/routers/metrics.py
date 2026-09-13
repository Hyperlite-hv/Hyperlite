"""Exposition des metriques -- chantier 10 de la roadmap vSphere/vCenter.

Deux facades sur les memes donnees (voir app/core/metrics.py pour la
collecte) :
  - GET /metrics : format d'exposition Prometheus (texte brut), pour brancher
    un vrai Prometheus + Grafana derriere -- gauges "instantanees" (dernier
    echantillon raw), pas d'historique (c'est le role de Prometheus lui-meme
    une fois branche de le retenir).
  - GET /vms/{name}/metrics/history et /host/metrics/history : historique
    persiste, consomme par les graphes internes du dashboard (1h -> palier
    raw, 24h/semaine/mois -> palier hourly, voir metrics.py pour le detail
    des paliers).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_conn
from app.core.security import get_current_user

router = APIRouter(tags=["metrics"])

_RANGES = {
    "1h": (timedelta(hours=1), "raw"),
    "24h": (timedelta(hours=24), "hourly"),
    "7j": (timedelta(days=7), "hourly"),
    "30j": (timedelta(days=30), "hourly"),
}


def _history(cible, range_key):
    if range_key not in _RANGES:
        raise HTTPException(status_code=422, detail=f"range invalide, attendu l'un de {list(_RANGES)}")
    delta, tier = _RANGES[range_key]
    since = (datetime.now(timezone.utc) - delta).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, cpu_pct, mem_used_mb, mem_total_mb, disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps "
            "FROM metrics_samples WHERE cible = ? AND tier = ? AND ts >= ? ORDER BY ts ASC",
            (cible, tier, since),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/vms/{name}/metrics/history")
def get_vm_metrics_history(name: str, range: str = "1h", user: dict = Depends(get_current_user)):
    return _history(name, range)


@router.get("/host/metrics/history")
def get_host_metrics_history(range: str = "1h", user: dict = Depends(get_current_user)):
    return _history("host", range)


def _latest_by_cible():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.* FROM metrics_samples m "
            "INNER JOIN (SELECT cible, MAX(ts) AS max_ts FROM metrics_samples WHERE tier='raw' GROUP BY cible) latest "
            "ON m.cible = latest.cible AND m.ts = latest.max_ts WHERE m.tier='raw'"
        ).fetchall()
    return [dict(r) for r in rows]


def _task_stats():
    with get_conn() as conn:
        running = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE statut = 'en_cours'").fetchone()["n"]
        total = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        failed = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE statut = 'echec'").fetchone()["n"]
        avg_row = conn.execute(
            "SELECT AVG((julianday(fin_le) - julianday(debut_le)) * 86400) AS avg_s "
            "FROM tasks WHERE statut = 'termine' AND fin_le IS NOT NULL"
        ).fetchone()
    return {
        "running": running, "total": total, "failed": failed,
        "avg_duration_s": round(avg_row["avg_s"], 2) if avg_row["avg_s"] is not None else 0,
    }


@router.get("/metrics")
def prometheus_metrics(user: dict = Depends(get_current_user)):
    """Format d'exposition Prometheus (text/plain), voir
    https://prometheus.io/docs/instrumenting/exposition_formats/ -- pas de
    lib externe necessaire, le format est volontairement simple a generer a
    la main pour un nombre de series aussi reduit."""
    lines = []

    def gauge(metric, help_text):
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")

    gauge("hyperlite_cpu_percent", "Utilisation CPU (%), hôte ou VM")
    gauge("hyperlite_memory_used_mb", "Mémoire utilisée (Mo)")
    gauge("hyperlite_memory_total_mb", "Mémoire totale/allouée (Mo)")
    gauge("hyperlite_disk_read_bytes_per_second", "Débit de lecture disque (o/s)")
    gauge("hyperlite_disk_write_bytes_per_second", "Débit d'écriture disque (o/s)")
    gauge("hyperlite_network_rx_bytes_per_second", "Débit réseau entrant (o/s)")
    gauge("hyperlite_network_tx_bytes_per_second", "Débit réseau sortant (o/s)")

    for row in _latest_by_cible():
        labels = f'{{scope="{row["scope"]}",target="{row["cible"]}"}}'
        for metric, field in [
            ("hyperlite_cpu_percent", "cpu_pct"),
            ("hyperlite_memory_used_mb", "mem_used_mb"),
            ("hyperlite_memory_total_mb", "mem_total_mb"),
            ("hyperlite_disk_read_bytes_per_second", "disk_read_bps"),
            ("hyperlite_disk_write_bytes_per_second", "disk_write_bps"),
            ("hyperlite_network_rx_bytes_per_second", "net_rx_bps"),
            ("hyperlite_network_tx_bytes_per_second", "net_tx_bps"),
        ]:
            if row[field] is not None:
                lines.append(f"{metric}{labels} {row[field]}")

    stats = _task_stats()
    gauge("hyperlite_jobs_running", "Tâches actuellement en cours")
    lines.append(f"hyperlite_jobs_running {stats['running']}")
    gauge("hyperlite_jobs_total", "Nombre total de tâches enregistrées")
    lines.append(f"hyperlite_jobs_total {stats['total']}")
    gauge("hyperlite_jobs_failed_total", "Nombre total de tâches en échec")
    lines.append(f"hyperlite_jobs_failed_total {stats['failed']}")
    gauge("hyperlite_jobs_avg_duration_seconds", "Durée moyenne des tâches terminées (s)")
    lines.append(f"hyperlite_jobs_avg_duration_seconds {stats['avg_duration_s']}")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
