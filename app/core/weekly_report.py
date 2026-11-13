"""Weekly report: what happened in the cluster over the last seven days, sent to the notification channels that take
the "weekly_report" event, on the weekday and hour an administrator chose (the node's local time).

The leader sends it, once per week: the last sending is recorded in the replicated settings, so a new leader does
not send it again. build() is pure: it turns plain facts into the report, which tests read without a server.
"""

import json
import logging
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

SETTINGS_KEY = "weekly_report"
SENT_KEY = "weekly_report_sent"
DEFAULTS = {"enabled": False, "weekday": 0, "hour": 8}
CHECK_INTERVAL_S = 300
PERIOD = timedelta(days=7)


def _settings_store():
    from app.repositories import registry

    return registry.settings().sync


def _report_store():
    from app.repositories.sqlite.report import SqliteReportStore

    return SqliteReportStore()


def settings():
    try:
        stored = json.loads(_settings_store().app_setting(SETTINGS_KEY) or "{}")
    except ValueError:
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def last_sent():
    return _settings_store().app_setting(SENT_KEY)


def save_settings(enabled, weekday, hour):
    if not isinstance(weekday, int) or not 0 <= weekday <= 6:
        raise ValueError("The weekday is 0 (Monday) to 6 (Sunday)")
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("The hour is 0 to 23")
    value = {"enabled": bool(enabled), "weekday": weekday, "hour": hour}
    _settings_store().set_app_setting(SETTINGS_KEY, json.dumps(value))
    return value


def _vm_name(target):
    return target.rsplit(":", 1)[-1]


def build(facts, now):
    """The report from `facts`: {"backups", "journal", "load", "pools"} as SqliteReportStore gives them."""
    backups = facts["backups"]
    ok = [b for b in backups if b["status"] == "succeeded"]
    failed = [b for b in backups if b["status"] == "failed"]
    journal = facts["journal"]
    failures = Counter(
        e["action"]
        for e in journal
        if e["result"] == "failed" and not e["action"].startswith(("get_", "list_", "login"))
    )
    alerts = Counter(e["resource"] for e in journal if e["action"] in ("alert_rule_fired", "alert_threshold_exceeded"))
    vms = [r for r in facts["load"] if r["scope"] == "vm" and r["cpu_avg"] is not None]
    hosts = [r for r in facts["load"] if r["scope"] == "host"]
    pools = []
    for p in facts["pools"]:
        if p["capacity_b"]:
            pools.append(
                {"node": p["node"], "pool": p["pool"], "used_pct": round(p["allocation_b"] * 100 / p["capacity_b"], 1)}
            )
    return {
        "period": {"from": (now - PERIOD).isoformat(), "to": now.isoformat()},
        "backups": {
            "succeeded": len(ok),
            "failed": len(failed),
            "bytes": sum(b["size_bytes"] or 0 for b in ok),
            "failed_vms": sorted({b["vm_name"] for b in failed}),
        },
        "failures": failures.most_common(5),
        "alerts": alerts.most_common(5),
        "busiest_vms": [
            {"vm": _vm_name(r["target"]), "cpu_avg": round(r["cpu_avg"], 1), "cpu_max": round(r["cpu_max"] or 0, 1)}
            for r in sorted(vms, key=lambda r: r["cpu_avg"], reverse=True)[:5]
        ],
        "nodes": [
            {
                "node": "this node" if r["target"] == "host" else r["target"].removeprefix("node:"),
                "cpu_avg": round(r["cpu_avg"] or 0, 1),
                "cpu_max": round(r["cpu_max"] or 0, 1),
                "mem_max": round(r["mem_max"] or 0, 1),
            }
            for r in sorted(hosts, key=lambda r: r["target"])
        ],
        "fullest_pools": sorted(pools, key=lambda p: p["used_pct"], reverse=True)[:5],
    }


def _size(n):
    for unit, size in (("TiB", 1024**4), ("GiB", 1024**3), ("MiB", 1024**2)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def text(report):
    """The report as the message a person reads in an email or a chat channel."""
    b = report["backups"]
    lines = [f"Week from {report['period']['from'][:10]} to {report['period']['to'][:10]}", ""]
    lines.append(f"Backups: {b['succeeded']} succeeded ({_size(b['bytes'])}), {b['failed']} failed")
    if b["failed_vms"]:
        lines.append(f"  Failed for: {', '.join(b['failed_vms'])}")
    lines += ["", "Nodes (processor average / peak, memory peak):"]
    lines += [
        f"  {n['node']}: {n['cpu_avg']} % / {n['cpu_max']} %, memory {n['mem_max']} %" for n in report["nodes"]
    ] or ["  no samples"]
    lines += ["", "Busiest VMs (processor average / peak):"]
    lines += [f"  {v['vm']}: {v['cpu_avg']} % / {v['cpu_max']} %" for v in report["busiest_vms"]] or ["  no samples"]
    lines += ["", "Fullest storage pools:"]
    lines += [f"  {p['pool']} ({p['node']}): {p['used_pct']} %" for p in report["fullest_pools"]] or ["  no samples"]
    lines += ["", "Alerts:"]
    lines += [f"  {who}: {n}" for who, n in report["alerts"]] or ["  none"]
    lines += ["", "Failed operations:"]
    lines += [f"  {action}: {n}" for action, n in report["failures"]] or ["  none"]
    return "\n".join(lines)


def current(now=None):
    now = now or datetime.now(UTC)
    since = (now - PERIOD).isoformat()
    store = _report_store()
    facts = {
        "backups": store.backups_since(since),
        "journal": store.journal_since(since),
        "load": store.load_since(since),
        "pools": store.pools_latest((now - timedelta(days=1)).isoformat()),
    }
    return build(facts, now)


def send(now=None, username="scheduler"):
    """Builds and sends the report now; returns it."""
    from app.core import notifications
    from app.core.audit import log_action

    now = now or datetime.now(UTC)
    report = current(now)
    notifications.notify(
        "weekly_report", f"Weekly report, week of {now.astimezone():%Y-%m-%d}", text(report), "success"
    )
    _settings_store().set_app_setting(SENT_KEY, now.isoformat())
    log_action(
        username, "weekly_report_sent", "notifications", "success", None
    )  # not "weekly_report": the journal would notify it again
    return report


def due(now, conf, last_sent):
    """Whether the report is due at `now`: the chosen weekday and hour have come (local time), and it was not sent
    within the last six days."""
    if not conf["enabled"]:
        return False
    local = now.astimezone()
    if local.weekday() != conf["weekday"] or local.hour < conf["hour"]:
        return False
    if last_sent:
        try:
            if now - datetime.fromisoformat(last_sent) < timedelta(days=6):
                return False
        except ValueError:
            pass
    return True


def tick(now=None):
    from app.core import cluster_lead

    if not cluster_lead.is_leader():
        return
    now = now or datetime.now(UTC)
    if due(now, settings(), last_sent()):
        send(now)


def _loop():
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Weekly report tick failed")
        time.sleep(CHECK_INTERVAL_S)


def start_weekly_report():
    threading.Thread(target=_loop, daemon=True, name="weekly-report").start()
