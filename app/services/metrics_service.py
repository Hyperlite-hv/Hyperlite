"""Metrics as the API shows them: histories of a VM, a node or the host, storage usage over time, and the figures the
Prometheus endpoint exposes. Samples are written by the collector thread (app/core/metrics.py)."""

from datetime import UTC, datetime, timedelta

from app.repositories import registry

# Range → (length, tier): the raw tier is kept a few hours, the hourly one a month.
RANGES = {
    "1h": (timedelta(hours=1), "raw"),
    "24h": (timedelta(hours=24), "hourly"),
    "7j": (timedelta(days=7), "hourly"),
    "30j": (timedelta(days=30), "hourly"),
}


class BadRange(ValueError):
    def __init__(self):
        super().__init__(f"Invalid range, expected one of {list(RANGES)}")


def _window(range_key):
    if range_key not in RANGES:
        raise BadRange()
    delta, tier = RANGES[range_key]
    return tier, (datetime.now(UTC) - delta).isoformat()


async def history(cible, range_key):
    tier, since = _window(range_key)
    return await registry.metrics().history(cible, tier, since)


async def storage_history(range_key, node=None):
    tier, since = _window(range_key)
    grouped = {}
    for r in await registry.metrics().storage_history(tier, since, node):
        key = (r["node"], r["pool"])
        grouped.setdefault(key, {"node": r["node"], "pool": r["pool"], "points": []})["points"].append(
            {"ts": r["ts"], "capacity_b": r["capacity_b"], "allocation_b": r["allocation_b"]}
        )
    return list(grouped.values())


# The Prometheus endpoint builds its text from libvirt as well: it runs in a worker thread, synchronously.


def latest_by_cible():
    return registry.metrics().sync.latest_by_cible()


def task_stats():
    return registry.tasks().sync.stats()


def latest_tick():
    return registry.metrics().sync.latest_tick()
