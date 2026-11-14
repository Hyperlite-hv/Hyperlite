"""The compliance review (docs/design/governance.md, section 1): the VMs already there checked against the guardrails
that new VMs follow (app/core/guardrails.py), and against their pools' envelopes.

The guardrails are checked when a VM is created; a rule added later, a VM resized or moved into a pool, or a VM made
before the rule, can break one. This lists every VM of this node with what it breaks, and fixes what can be fixed
without touching the guest (a required tag is added); the rest says what to change.
"""

import re

from app.core import guardrails, object_meta
from app.core import permissions as perm


def _check(vm, rule, where, tags):
    found = []
    if rule.get("name_pattern"):
        try:
            if not re.fullmatch(rule["name_pattern"], vm["name"]):
                found.append(("name", f"the name does not follow {rule['name_pattern']}", False))
        except re.error:
            pass
    disk = sum(d["size_gb"] or 0 for d in vm["disks"])
    for field, value, unit in (("max_vcpu", vm["vcpu"], "vCPUs"), ("max_memory_mb", vm["memory_mb"], "MB of memory"),
                               ("max_disk_gb", disk, "GB of disk")):  # fmt: skip
        if rule.get(field) and value > rule[field]:
            found.append((field, f"{value} {unit}, {rule[field]} allowed", False))
    for field, chosen, label in (
        ("storage_pools", {d["storage_pool"] for d in vm["disks"] if d["storage_pool"]}, "storage pool"),
        ("networks", {i["network"] for i in vm["interfaces"] if i["network"]}, "network"),
    ):
        allowed = rule.get(field)
        outside = sorted(chosen - set(allowed)) if allowed else []
        if outside:
            found.append((field, f"{label} {', '.join(outside)} not allowed", False))
    missing = [t for t in rule.get("required_tags") or [] if t not in tags]
    if missing:
        found.append(("required_tags", f"missing tag{'s' if len(missing) > 1 else ''} {', '.join(missing)}", True))
    return [{"rule": r, "scope": where, "detail": d, "fixable": f} for r, d, f in found]


def review():
    """Every VM of this node with the guardrails and envelopes it breaks, and the totals."""
    from app.core import self_node, vm_definition
    from app.core.libvirt_utils import open_conn

    held = guardrails.rules()
    pools = {p["id"]: p["name"] for p in perm.list_pools()}
    envelopes = guardrails.envelopes()
    over = {}
    for pool_id in envelopes:
        if not str(pool_id).isdigit() or int(pool_id) not in pools:
            continue
        broken = guardrails.envelope_refusal(int(pool_id))
        env, used = envelopes[pool_id], guardrails.usage(int(pool_id))
        exceeded = [
            f"{used[u]} {label} for {env[f]}"
            for f, u, label in (
                ("max_vms", "vms", "VMs"),
                ("max_vcpus", "vcpus", "vCPUs"),
                ("max_memory_mb", "memory_mb", "MB"),
                ("max_disk_gb", "disk_gb", "GB"),
            )
            if env[f] is not None and used[u] > env[f]
        ]
        if exceeded or broken:
            over[int(pool_id)] = "; ".join(exceeded)
    rows = []
    conn = open_conn(readonly=True)
    try:
        for domain in conn.listAllDomains():
            vm = vm_definition.read(conn, domain)
            vm_pools = perm.get_vm_pools(vm["name"])
            tags = object_meta.effective_tags("vm", vm["name"])
            issues = []
            if held.get("cluster"):
                issues += _check(vm, held["cluster"], "cluster", tags)
            for pool_id in vm_pools:
                rule = held.get(f"pool:{pool_id}")
                if rule:
                    issues += _check(vm, rule, f"pool:{pool_id}", tags)
                if pool_id in over:
                    issues.append(
                        {"rule": "envelope", "scope": f"pool:{pool_id}", "detail": over[pool_id], "fixable": False}
                    )
            rows.append(
                {
                    "name": vm["name"],
                    "pools": [pools.get(p, str(p)) for p in vm_pools],
                    "tags": tags,
                    "issues": issues,
                }
            )
    finally:
        conn.close()
    compliant = sum(1 for r in rows if not r["issues"])
    return {
        "node": self_node.name(),
        "vms": sorted(rows, key=lambda r: (not r["issues"], r["name"])),
        "summary": {
            "vms": len(rows),
            "compliant": compliant,
            "percent": round(100 * compliant / len(rows)) if rows else 100,
            "fixable": sum(1 for r in rows if any(i["fixable"] for i in r["issues"])),
        },
        "rules": len(held),
    }


def fix(vm_name):
    """Add the tags the guardrails require and the VM lacks (its own tags; the rest is left as it is). Returns the
    tags added."""
    held = guardrails.rules()
    scopes = ["cluster", *(f"pool:{p}" for p in perm.get_vm_pools(vm_name))]
    tags = object_meta.effective_tags("vm", vm_name)
    wanted = []
    for scope in scopes:
        for t in (held.get(scope) or {}).get("required_tags") or []:
            if t not in tags and t not in wanted:
                wanted.append(t)
    if wanted:
        own = object_meta.get("vm", vm_name)
        object_meta.put("vm", vm_name, own["notes"], [*own["tags"], *wanted])
    return wanted
