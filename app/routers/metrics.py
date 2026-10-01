"""Metrics exposure.

Two facades over the same data (see app/core/metrics.py for collection):
  - GET /metrics: Prometheus exposition format (plain text), to plug a real
    Prometheus + Grafana behind it. These are "instantaneous" gauges (latest
    raw sample) with no history: retaining history is Prometheus' own job once
    connected.
  - GET /vms/{name}/metrics/history and /host/metrics/history: persisted
    history, consumed by the dashboard's internal charts (1h -> raw tier,
    24h/week/month -> hourly tier, see metrics.py for the tiers).

"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user
from app.services import metrics_service

router = APIRouter(tags=["metrics"])


async def _history(cible, range_key):
    try:
        return await metrics_service.history(cible, range_key)
    except metrics_service.BadRange as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/vms/{name}/metrics/history")
async def get_vm_metrics_history(
    name: str, range: str = "1h", node: str | None = None, user: dict = Depends(get_current_user)
):
    """node: the VM's node, the same convention as GET /vms (omitted or 'local' = this host).
    VMs of a remote node are sampled under '<node>:<vm>' so equal names on two nodes never mix."""
    return await _history(name if not node or node == "local" else f"{node}:{name}", range)


@router.get("/host/metrics/history")
async def get_host_metrics_history(range: str = "1h", user: dict = Depends(get_current_user)):
    return await _history("host", range)


@router.get("/nodes/{name}/metrics/history")
async def get_node_metrics_history(name: str, range: str = "1h", user: dict = Depends(get_current_user)):
    """History of one node: 'local' is this host, any other name a registered remote node."""
    return await _history("host" if name == "local" else f"node:{name}", range)


@router.get("/storage/history")
async def get_storage_history(range: str = "24h", node: str | None = None, user: dict = Depends(get_current_user)):
    """Usage of every storage pool over time, grouped by node and pool."""
    try:
        return await metrics_service.storage_history(range, node)
    except metrics_service.BadRange as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


def _latest_by_cible():
    return metrics_service.latest_by_cible()


def _task_stats():
    return metrics_service.task_stats()


@router.get("/metrics")
def prometheus_metrics(user: dict = Depends(get_current_user)):
    """Prometheus exposition format (text/plain), see
    https://prometheus.io/docs/instrumenting/exposition_formats/. No external
    library is needed: the format is deliberately simple to generate by hand
    for such a small number of series."""
    lines = []

    def gauge(metric, help_text):
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")

    gauge("hyperlite_cpu_percent", "CPU usage (%), host or VM")
    gauge("hyperlite_memory_used_mb", "Memory used (MiB)")
    gauge("hyperlite_memory_total_mb", "Total/allocated memory (MiB)")
    gauge("hyperlite_disk_read_bytes_per_second", "Disk read throughput (bytes/s)")
    gauge("hyperlite_disk_write_bytes_per_second", "Disk write throughput (bytes/s)")
    gauge("hyperlite_network_rx_bytes_per_second", "Incoming network throughput (bytes/s)")
    gauge("hyperlite_network_tx_bytes_per_second", "Outgoing network throughput (bytes/s)")

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
    gauge("hyperlite_jobs_running", "Tasks currently running")
    lines.append(f"hyperlite_jobs_running {stats['running']}")
    gauge("hyperlite_jobs_total", "Total number of recorded tasks")
    lines.append(f"hyperlite_jobs_total {stats['total']}")
    gauge("hyperlite_jobs_failed_total", "Total number of failed tasks")
    lines.append(f"hyperlite_jobs_failed_total {stats['failed']}")
    gauge("hyperlite_jobs_avg_duration_seconds", "Average duration of finished tasks (s)")
    lines.append(f"hyperlite_jobs_avg_duration_seconds {stats['avg_duration_s']}")

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
