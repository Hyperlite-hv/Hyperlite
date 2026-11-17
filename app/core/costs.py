"""Costs and budgets (docs/design/governance.md, section 10): what the VMs cost, by the prices an administrator sets.

A month of a VM costs its vCPUs and memory while it runs (a stopped VM holds none) and its disk all the time, at the
prices per vCPU, per GB of memory and per GB of disk. Costs are shown per VM, pool, tag and in total, for the VMs of
the answering node, as the month's estimate. A budget (on the cluster, a pool or a tag) alerts once when the estimate
reaches its threshold, and once more when it is exceeded.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn

PRICES = "cost_prices"
DEFAULT_PRICES = {"currency": "EUR", "vcpu": 0.0, "memory_gb": 0.0, "disk_gb": 0.0}


def _settings():
    from app.repositories import registry

    return registry.settings().sync


def prices():
    raw = _settings().app_setting(PRICES)
    try:
        return {**DEFAULT_PRICES, **(json.loads(raw) if raw else {})}
    except ValueError:
        return dict(DEFAULT_PRICES)


def set_prices(p):
    _settings().set_app_setting(PRICES, json.dumps({k: p[k] for k in DEFAULT_PRICES}))


def vm_cost(vm, running, p):
    compute = (vm["vcpu"] * p["vcpu"] + vm["memory_mb"] / 1024 * p["memory_gb"]) if running else 0.0
    disk = sum(d["size_gb"] or 0 for d in vm["disks"]) * p["disk_gb"]
    return round(compute + disk, 2)


def report():
    """Each VM of this node with its monthly cost, and the totals per pool, per tag and overall."""
    from app.core import object_meta, self_node, vm_definition
    from app.core import permissions as perm
    from app.core.libvirt_utils import open_conn

    p = prices()
    pools = {x["id"]: x["name"] for x in perm.list_pools()}
    vms = []
    conn = open_conn(readonly=True)
    try:
        for domain in conn.listAllDomains():
            vm = vm_definition.read(conn, domain)
            vms.append(
                {
                    "name": vm["name"],
                    "running": bool(domain.isActive()),
                    "vcpu": vm["vcpu"],
                    "memory_mb": vm["memory_mb"],
                    "disk_gb": sum(d["size_gb"] or 0 for d in vm["disks"]),
                    "pools": [pools.get(i, str(i)) for i in perm.get_vm_pools(vm["name"])],
                    "pool_ids": perm.get_vm_pools(vm["name"]),
                    "tags": object_meta.effective_tags("vm", vm["name"]),
                    "monthly": vm_cost(vm, bool(domain.isActive()), p),
                }
            )
    finally:
        conn.close()
    by_pool, by_tag = {}, {}
    for v in vms:
        for name in v["pools"]:
            by_pool[name] = round(by_pool.get(name, 0) + v["monthly"], 2)
        for t in v["tags"]:
            by_tag[t] = round(by_tag.get(t, 0) + v["monthly"], 2)
    return {
        "node": self_node.name(),
        "prices": p,
        "vms": sorted(vms, key=lambda v: -v["monthly"]),
        "by_pool": by_pool,
        "by_tag": by_tag,
        "total": round(sum(v["monthly"] for v in vms), 2),
    }


def _spent(scope, rep):
    if scope == "cluster":
        return rep["total"]
    kind, _, ident = scope.partition(":")
    if kind == "pool":
        return round(sum(v["monthly"] for v in rep["vms"] if ident.isdigit() and int(ident) in v["pool_ids"]), 2)
    if kind == "tag":
        return rep["by_tag"].get(ident, 0.0)
    return 0.0


def budgets(rep=None):
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM cost_budgets ORDER BY name")]
    if rows:
        from app.core import cluster_lead

        rep = rep or report()
        clustered = cluster_lead.in_cluster()
        for b in rows:
            b["spent"] = _spent(b["scope"], rep)
            if clustered:
                # Every node's VMs (cost_spend), this node's own counted as it is now.
                b["spent"] = max(b["spent"], cluster_spend(b["id"]))
            b["percent"] = round(100 * b["spent"] / b["monthly"]) if b["monthly"] else 0
    return rows


def save_budget(fields, username, budget_id=None):
    scope = fields["scope"]
    if scope != "cluster" and not (scope.startswith("pool:") and scope[5:].isdigit()) and not scope.startswith("tag:"):
        raise ValueError("A budget is on 'cluster', 'pool:<id>' or 'tag:<tag>'")
    with get_conn() as db:
        if budget_id:
            cur = db.execute(
                "UPDATE cost_budgets SET name = ?, scope = ?, monthly = ?, alert_percent = ? WHERE id = ?",
                (fields["name"], scope, fields["monthly"], fields["alert_percent"], budget_id),
            )
            if cur.rowcount == 0:
                return None
        else:
            budget_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO cost_budgets (id, name, scope, monthly, alert_percent, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                (budget_id, fields["name"], scope, fields["monthly"], fields["alert_percent"], username),
            )
        db.commit()
    return next((b for b in budgets() if b["id"] == budget_id), None)


def delete_budget(budget_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM cost_budgets WHERE id = ?", (budget_id,))
        db.commit()
    return cur.rowcount > 0


SPEND_FRESH_S = 3 * 3600  # a node silent for longer counts no more (removed, or down for long)


def publish_spend(rows, now=None):
    """This node's VMs' spend for each budget, for the cluster's sum (cost_spend)."""
    from app.core import self_node

    at = (now or datetime.now(UTC)).isoformat()
    me = self_node.name()
    with get_conn() as db:
        db.execute("DELETE FROM cost_spend WHERE node = ? AND budget_id NOT IN (SELECT id FROM cost_budgets)", (me,))
        for b in rows:
            db.execute(
                "INSERT INTO cost_spend (node, budget_id, spent, at) VALUES (?, ?, ?, ?) ON CONFLICT(node, budget_id) "
                "DO UPDATE SET spent = excluded.spent, at = excluded.at",
                (me, b["id"], b["spent"], at),
            )
        db.commit()


def cluster_spend(budget_id, now=None):
    """The budget's spend over every node that published one recently."""
    since = ((now or datetime.now(UTC)) - timedelta(seconds=SPEND_FRESH_S)).isoformat()
    with get_conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(spent), 0) FROM cost_spend WHERE budget_id = ? AND at >= ?", (budget_id, since)
        ).fetchone()
    return round(row[0], 2)


def check_budgets(now=None):
    """Alert once per month per level (1: threshold reached, 2: exceeded); hyperlite-scheduler, every 15 minutes. Each
    node publishes its own VMs' spend; in a cluster only the leader alerts, on the sum of every node's."""
    from app.core import cluster_lead
    from app.core.audit import log_action

    month = (now or datetime.now(UTC)).strftime("%Y-%m")
    rows = budgets()
    publish_spend(rows, now)
    if cluster_lead.in_cluster():
        if not cluster_lead.is_leader():
            return
        for b in rows:
            b["spent"] = cluster_spend(b["id"], now)
            b["percent"] = round(100 * b["spent"] / b["monthly"]) if b["monthly"] else 0
    for b in rows:
        level = 2 if b["percent"] >= 100 else 1 if b["percent"] >= b["alert_percent"] else 0
        done = b["alerted_level"] if b["alerted_month"] == month else 0
        if level > done:
            what = "exceeded" if level == 2 else f"at {b['percent']} %"
            log_action("system", "cost_budget", b["name"], "success",
                       f"{what}: {b['spent']} of {b['monthly']} {prices()['currency']} this month")  # fmt: skip
            with get_conn() as db:
                db.execute(
                    "UPDATE cost_budgets SET alerted_month = ?, alerted_level = ? WHERE id = ?", (month, level, b["id"])
                )
                db.commit()


CHECK_EVERY_S = 900
_last_check = {"at": 0.0}


def check_budgets_if_due():
    """check_budgets at most every 15 minutes: the report reads every VM's disks."""
    import time

    if time.monotonic() - _last_check["at"] >= CHECK_EVERY_S:
        _last_check["at"] = time.monotonic()
        if _has_budgets():
            check_budgets()


def _has_budgets():
    with get_conn() as db:
        return db.execute("SELECT 1 FROM cost_budgets LIMIT 1").fetchone() is not None
