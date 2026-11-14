"""Envelopes and guardrails (docs/design/access-2.md, section 5).

An envelope caps what a pool of VMs holds: how many VMs, and their vCPUs, memory and disk together. A guardrail is a
creation rule held on the cluster or on one pool: the largest VM (vCPU, memory, disk), the storage pools and networks
allowed, and a name pattern. Both are checked when a VM is created (and an envelope when one of its members grows);
administrators are held by them too, and change them.

The resources a pool holds are those of its members this node hosts (libvirt) plus, for members on other nodes, the
count only: a pool is the cluster's, its VMs' sizes are each node's own.
"""

import json
import re
from datetime import UTC, datetime

import libvirt

from app.core.database import get_conn

ENVELOPE_FIELDS = ("max_vms", "max_vcpus", "max_memory_mb", "max_disk_gb")
RULE_FIELDS = ("name_pattern", "max_vcpu", "max_memory_mb", "max_disk_gb", "storage_pools", "networks")


def _now():
    return datetime.now(UTC).isoformat()


# ---- Envelopes ----


def envelopes():
    with get_conn() as db:
        return {r["pool_id"]: dict(r) for r in db.execute("SELECT * FROM pool_envelopes")}


def envelope(pool_id):
    return envelopes().get(str(pool_id))


def set_envelope(pool_id, limits, username):
    values = [limits.get(f) for f in ENVELOPE_FIELDS]
    with get_conn() as db:
        if all(v is None for v in values):
            db.execute("DELETE FROM pool_envelopes WHERE pool_id = ?", (str(pool_id),))
        else:
            db.execute(
                "INSERT INTO pool_envelopes (pool_id, max_vms, max_vcpus, max_memory_mb, max_disk_gb, changed_by, "
                "changed_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pool_id) DO UPDATE SET max_vms = excluded.max_vms, "
                "max_vcpus = excluded.max_vcpus, max_memory_mb = excluded.max_memory_mb, "
                "max_disk_gb = excluded.max_disk_gb, changed_by = excluded.changed_by, changed_at = excluded.changed_at",
                (str(pool_id), *values, username, _now()),
            )
        db.commit()
    return envelope(pool_id)


def _disk_gb(domain):
    """The VM's disks' capacity together, in GB (rounded up per disk)."""
    import xml.etree.ElementTree as ET

    total = 0
    root = ET.fromstring(domain.XMLDesc(0))
    for disk in root.findall("./devices/disk[@device='disk']"):
        target = disk.find("target")
        if target is None:
            continue
        try:
            capacity = domain.blockInfo(target.get("dev"))[0]
        except libvirt.libvirtError:
            continue
        total += -(-capacity // 1024**3)
    return total


def usage(pool_id):
    """What the pool holds: its VMs (every member, wherever it runs) and the vCPUs, memory and disk of those this
    node hosts."""
    from app.core import permissions
    from app.core.libvirt_utils import open_conn

    members = next((p["vms"] for p in permissions.list_pools() if p["id"] == int(pool_id)), [])
    used = {"vms": len(members), "vcpus": 0, "memory_mb": 0, "disk_gb": 0, "measured": 0}
    if not members:
        return used
    conn = open_conn(readonly=True)
    try:
        for name in members:
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                continue  # on another node
            info = domain.info()
            used["vcpus"] += info[3]
            used["memory_mb"] += info[1] // 1024
            used["disk_gb"] += _disk_gb(domain)
            used["measured"] += 1
    finally:
        conn.close()
    return used


def envelope_refusal(pool_id, vms=0, vcpus=0, memory_mb=0, disk_gb=0):
    """Why adding that much to the pool would break its envelope, or None (no envelope, or room enough)."""
    env = envelope(pool_id)
    if env is None:
        return None
    used = usage(pool_id)
    over = []
    for field, add, label, unit in (
        ("max_vms", vms, "vms", "VMs"),
        ("max_vcpus", vcpus, "vcpus", "vCPUs"),
        ("max_memory_mb", memory_mb, "memory_mb", "MB of memory"),
        ("max_disk_gb", disk_gb, "disk_gb", "GB of disk"),
    ):
        limit = env[field]
        if limit is not None and add > 0 and used[label] + add > limit:
            over.append(f"{used[label] + add} {unit} for {limit} allowed")
    if not over:
        return None
    return f"The envelope of pool {pool_id} would be exceeded: {'; '.join(over)}"


# ---- Guardrails ----


def _rule(row):
    rule = dict(row)
    for f in ("storage_pools", "networks"):
        rule[f] = json.loads(rule[f]) if rule[f] else None
    return rule


def rules():
    with get_conn() as db:
        found = {r["scope"]: _rule(r) for r in db.execute("SELECT * FROM guardrails")}
        for r in db.execute("SELECT scope, required_tags FROM guardrail_tags"):
            found.setdefault(r["scope"], {"scope": r["scope"], **dict.fromkeys(RULE_FIELDS)})
            found[r["scope"]]["required_tags"] = json.loads(r["required_tags"] or "[]")
    for rule in found.values():
        rule.setdefault("required_tags", [])
    return found


def set_rule(scope, rule, username):
    if scope != "cluster" and not re.fullmatch(r"pool:[0-9]+", scope):
        raise ValueError("A guardrail is held on 'cluster' or 'pool:<id>'")
    if rule.get("name_pattern"):
        try:
            re.compile(rule["name_pattern"])
        except re.error as e:
            raise ValueError(f"The name pattern is not a valid regular expression: {e}") from None
    values = [
        json.dumps(sorted(set(rule[f]))) if f in ("storage_pools", "networks") and rule.get(f) else rule.get(f) or None
        for f in RULE_FIELDS
    ]
    from app.core import object_meta

    try:
        required = object_meta.normalize_tags(rule.get("required_tags") or [])
    except object_meta.MetaError as e:
        raise ValueError(str(e)) from None
    with get_conn() as db:
        if required:
            db.execute(
                "INSERT INTO guardrail_tags (scope, required_tags) VALUES (?, ?) ON CONFLICT(scope) DO UPDATE SET "
                "required_tags = excluded.required_tags",
                (scope, json.dumps(required)),
            )
        else:
            db.execute("DELETE FROM guardrail_tags WHERE scope = ?", (scope,))
        if all(v is None for v in values):
            db.execute("DELETE FROM guardrails WHERE scope = ?", (scope,))
        else:
            db.execute(
                "INSERT INTO guardrails (scope, name_pattern, max_vcpu, max_memory_mb, max_disk_gb, storage_pools, "
                "networks, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET "
                "name_pattern = excluded.name_pattern, max_vcpu = excluded.max_vcpu, "
                "max_memory_mb = excluded.max_memory_mb, max_disk_gb = excluded.max_disk_gb, "
                "storage_pools = excluded.storage_pools, networks = excluded.networks, "
                "changed_by = excluded.changed_by, changed_at = excluded.changed_at",
                (scope, *values, username, _now()),
            )
        db.commit()
    return rules().get(scope)


def forget_pool(pool_id):
    with get_conn() as db:
        db.execute("DELETE FROM guardrails WHERE scope = ?", (f"pool:{pool_id}",))
        db.execute("DELETE FROM guardrail_tags WHERE scope = ?", (f"pool:{pool_id}",))
        db.execute("DELETE FROM pool_envelopes WHERE pool_id = ?", (str(pool_id),))
        db.commit()


def creation_refusal(name, vcpu, memory_mb, disk_gb, storage_pools, networks, pool_id=None, tags=()):
    """Why a new VM breaks a guardrail of the cluster or of its pool, or an envelope of its pool, or None. `tags`: the
    tags it is created with; it also carries those of its pool."""
    from app.core import object_meta

    held = rules()
    tags = set(tags) | (set(object_meta.get("pool", str(pool_id))["tags"]) if pool_id is not None else set())
    for scope in ("cluster", f"pool:{pool_id}" if pool_id is not None else None):
        rule = held.get(scope) if scope else None
        if not rule:
            continue
        where = "the cluster" if scope == "cluster" else f"pool {pool_id}"
        if rule["name_pattern"] and not re.fullmatch(rule["name_pattern"], name):
            return f"The name '{name}' does not follow the pattern of {where}: {rule['name_pattern']}"
        if rule["max_vcpu"] and vcpu > rule["max_vcpu"]:
            return f"{vcpu} vCPUs: {where} allows {rule['max_vcpu']} at most per VM"
        if rule["max_memory_mb"] and memory_mb > rule["max_memory_mb"]:
            return f"{memory_mb} MB of memory: {where} allows {rule['max_memory_mb']} MB at most per VM"
        if rule["max_disk_gb"] and disk_gb > rule["max_disk_gb"]:
            return f"{disk_gb} GB of disk: {where} allows {rule['max_disk_gb']} GB at most per VM"
        missing = [t for t in rule.get("required_tags") or [] if t not in tags]
        if missing:
            return f"{where} requires the tag{'s' if len(missing) > 1 else ''} {', '.join(missing)} on every new VM"
        for kind, chosen, allowed in (
            ("storage pool", storage_pools, rule["storage_pools"]),
            ("network", networks, rule["networks"]),
        ):
            outside = sorted(set(chosen) - set(allowed)) if allowed else []
            if outside:
                return f"The {kind} '{outside[0]}' is not allowed in {where} (allowed: {', '.join(allowed)})"
    if pool_id is not None:
        return envelope_refusal(pool_id, vms=1, vcpus=vcpu, memory_mb=memory_mb, disk_gb=disk_gb)
    return None
