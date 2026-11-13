"""Alert rules (#348): "the processor of db-01 above 85 % for 10 minutes", written by administrators.

A rule names a scope (one node, one VM, every node, every VM), a metric, a comparison, a threshold and a window. Its
condition holds for a target when the average of the target's raw samples over the window crosses the threshold. The
leader evaluates every enabled rule after each collector tick (app/core/metrics.py): every node samples every node, so
the leader alone holds what it needs, and it alone notifies.

A rule fires once for a target when its condition starts holding, and says so again when it stops: the targets it
fires for are kept on the rule (replicated, written on a change only), so every node shows the same state and a new
leader carries on without repeating a notification.

Notified to the channels the rule names, else to every channel that takes the "alert_rule" event; written to the
journal as alert_rule_fired / alert_rule_resolved.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from app.core import self_node

logger = logging.getLogger(__name__)

# Metric → (unit, how to read it from a sample). Percentages of the memory are computed from the used and total.
METRICS = {
    "cpu_pct": ("%", lambda r: r.get("cpu_pct")),
    "mem_pct": (
        "%",
        lambda r: (
            (r["mem_used_mb"] / r["mem_total_mb"] * 100)
            if r.get("mem_total_mb") and r.get("mem_used_mb") is not None
            else None
        ),
    ),
    "disk_read_bps": ("B/s", lambda r: r.get("disk_read_bps")),
    "disk_write_bps": ("B/s", lambda r: r.get("disk_write_bps")),
    "net_rx_bps": ("B/s", lambda r: r.get("net_rx_bps")),
    "net_tx_bps": ("B/s", lambda r: r.get("net_tx_bps")),
}
OPERATORS = {"gt": ">", "lt": "<"}
SCOPES = ("node", "vm", "all_nodes", "all_vms")
SEVERITIES = ("critical", "warning", "info")
WINDOWS = (1, 5, 10, 15, 30, 60)
MAX_RULES = 200
MAX_CHANNELS = 20
NAME_RE = re.compile(r"^[^\x00-\x1f]{1,80}$")
VM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# Samples needed in the window before a condition is judged: one tick is not a trend.
MIN_SAMPLES = 2


def _store():
    from app.repositories.sqlite.alerts import SqliteAlertRulesStore

    return SqliteAlertRulesStore()


def _now():
    return datetime.now(UTC)


def validate(rule):
    """The rule as it is stored, or ValueError saying what is wrong."""
    name = str(rule.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("A rule needs a name of 1 to 80 characters")
    scope = rule.get("scope")
    if scope not in SCOPES:
        raise ValueError(f"The scope must be one of {', '.join(SCOPES)}")
    node = rule.get("node") or None
    vm = rule.get("vm") or None
    if scope in ("node", "vm"):
        if not node or not (node == self_node.LOCAL or self_node.NAME_RE.match(node)):
            raise ValueError("Not a node name")
    else:
        node = None
    if scope == "vm":
        if not vm or not VM_RE.match(vm):
            raise ValueError("Not a VM name")
    else:
        vm = None
    metric = rule.get("metric")
    if metric not in METRICS:
        raise ValueError(f"The metric must be one of {', '.join(METRICS)}")
    operator = rule.get("operator")
    if operator not in OPERATORS:
        raise ValueError("The operator must be gt or lt")
    try:
        threshold = float(rule.get("threshold"))
    except (TypeError, ValueError):
        raise ValueError("The threshold must be a number") from None
    if threshold < 0 or (METRICS[metric][0] == "%" and threshold > 100):
        raise ValueError("The threshold is out of range")
    window = rule.get("window_min", 5)
    if window not in WINDOWS:
        raise ValueError(f"The window must be one of {', '.join(map(str, WINDOWS))} minutes")
    severity = rule.get("severity", "warning")
    if severity not in SEVERITIES:
        raise ValueError(f"The severity must be one of {', '.join(SEVERITIES)}")
    channels = rule.get("channels") or []
    if not isinstance(channels, list) or len(channels) > MAX_CHANNELS or not all(isinstance(c, int) for c in channels):
        raise ValueError("The channels must be a list of channel ids")
    return {
        "name": name,
        "enabled": bool(rule.get("enabled", True)),
        "scope": scope,
        "node": self_node.to_db(node) if node else None,
        "vm": vm,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "window_min": window,
        "severity": severity,
        "channels": sorted(set(channels)),
    }


def _out(row):
    """A stored rule as the API shows it: "local" for this node, as everywhere else in the API, and who it fires for."""
    return {
        **row,
        "node": self_node.from_db(row["node"]) if row.get("node") else None,
        "firing_labels": firing_view(row),
    }


def rules():
    return [_out(r) for r in _store().rules()]


def rule(rule_id):
    r = _store().rule(rule_id)
    return _out(r) if r else None


def create(payload, username):
    clean = validate(payload)
    if len(_store().rules()) >= MAX_RULES:
        raise ValueError(f"At most {MAX_RULES} alert rules")
    rule_id = uuid.uuid4().hex[:16]
    _store().create(rule_id, clean, username, _now().isoformat())
    return rule(rule_id)


def update(rule_id, payload):
    """The rule saved again (its state starts over: a changed condition is a new one), or None when there is none."""
    if not _store().rule(rule_id):
        return None
    _store().update(rule_id, validate(payload))
    return rule(rule_id)


def set_enabled(rule_id, enabled):
    if not _store().rule(rule_id):
        return None
    _store().set_enabled(rule_id, enabled)
    return rule(rule_id)


def delete(rule_id):
    return _store().delete(rule_id)


# Evaluation


def stable_target(target, scope, self_name):
    """A metrics_samples target in names every node reads alike: the node's name for a node ("host" is the sampling
    node itself), "node:vm" for a VM (a VM of the sampling node is stored under its bare name)."""
    if scope == "host":
        return self_name if target == "host" else target.removeprefix("node:")
    return target if ":" in target else f"{self_name}:{target}"


def describe_target(key, scope):
    """A stable target as a person reads it: "db-01 (hv-02)" for a VM, "hv-02" for a node."""
    if scope == "host":
        return key
    node, _, vm = key.partition(":")
    return f"{vm} ({node})"


def _scope(rule):
    return "host" if rule["scope"] in ("node", "all_nodes") else "vm"


def check(rule, samples, since, self_name):
    """{stable target: average} of the targets whose condition holds over the window, from samples since `since`."""
    scope = _scope(rule)
    wanted = (
        {rule["node"]}
        if rule["scope"] == "node"
        else {f"{rule['node']}:{rule['vm']}"}
        if rule["scope"] == "vm"
        else None
    )
    pick = METRICS[rule["metric"]][1]
    values = {}
    for s in samples:
        if s["ts"] < since or s["scope"] != scope:
            continue
        key = stable_target(s["target"], scope, self_name)
        if wanted is not None and key not in wanted:
            continue
        v = pick(s)
        if v is not None:
            values.setdefault(key, []).append(v)
    out = {}
    for key, vals in values.items():
        if len(vals) < MIN_SAMPLES:
            continue
        avg = sum(vals) / len(vals)
        if (avg > rule["threshold"]) if rule["operator"] == "gt" else (avg < rule["threshold"]):
            out[key] = avg
    return out


def _format(value, metric):
    unit = METRICS[metric][0]
    if unit == "%":
        return f"{value:.1f} %"
    for size, suffix in ((1024**3, "GiB/s"), (1024**2, "MiB/s"), (1024, "KiB/s")):
        if value >= size:
            return f"{value / size:.1f} {suffix}"
    return f"{value:.0f} B/s"


def _send(rule, title, message, result):
    from app.core import notifications

    if not rule["channels"]:
        notifications.notify("alert_rule", title, message, result)
        return
    for channel in notifications.list_channels():
        if channel["id"] in rule["channels"] and channel["enabled"]:
            try:
                notifications.send_to_channel(channel, title, message, "alert_rule", result)
            except Exception as e:
                from app.core.audit import log_action

                log_action("system", "notification_failed", channel["name"], "failed", str(e)[:300])


def evaluate(now=None):
    """One round over the enabled rules: the leader's, after a collector tick. Never raises."""
    from app.core import cluster_lead

    if not cluster_lead.is_leader():
        return
    try:
        stored = [r for r in _store().rules() if r["enabled"]]
        if not stored:
            return
        now = now or _now()
        oldest = now - timedelta(minutes=max(r["window_min"] for r in stored))
        from app.repositories import registry

        samples = registry.metrics().sync.recent_raw(oldest.isoformat())
        self_name = self_node.name()
        for r in stored:
            since = (now - timedelta(minutes=r["window_min"])).isoformat()
            _transition(r, check(r, samples, since, self_name), now)
    except Exception:
        logger.warning("Alert rules not evaluated", exc_info=True)


def _transition(r, holding, now):
    from app.core.audit import log_action

    before = set(r["firing"])
    after = set(holding)
    if before == after:
        return
    scope = _scope(r)
    op = OPERATORS[r["operator"]]
    threshold = _format(r["threshold"], r["metric"])
    for target in sorted(after - before):
        who = describe_target(target, scope)
        detail = f"{r['metric']} = {_format(holding[target], r['metric'])} {op} {threshold} over {r['window_min']} min"
        log_action("system", "alert_rule_fired", who, "failed", f"{r['name']}: {detail}")
        _send(r, f"[{r['severity']}] {r['name']}: {who}", detail, "failed")
    for target in sorted(before - after):
        who = describe_target(target, scope)
        log_action(
            "system",
            "alert_rule_resolved",
            who,
            "success",
            f"{r['name']}: back {'under' if op == '>' else 'over'} {threshold}",
        )
        _send(
            r,
            f"[resolved] {r['name']}: {who}",
            f"{r['metric']} is back {'under' if op == '>' else 'over'} {threshold}",
            "success",
        )
    _store().set_firing(r["id"], sorted(after), now.isoformat())


def firing_view(r):
    """The targets a rule fires for, as a person reads them."""
    return [describe_target(t, _scope(r)) for t in r.get("firing") or []]
