"""HA admission control (docs/design/ha-automatic.md, 4.8, #333 step 5), as vSphere's: the loss of any one member must
leave room for its running HA VMs on the others.

For each member, its running HA VMs are placed, the largest first, on the other online members, each taking the
member with the most free memory left (each node publishes its free memory in its status, app/core/statd.py; it
already counts the VMs that run there). A member whose VMs do not all fit could not be absorbed. A member whose free
memory is not known (a node before this version) is neither counted as room nor checked: it is named, never refused
for.

The policy is the cluster's (`ha_settings`, replicated): `warn` (the default) accepts and says it; `strict` refuses to
turn HA on for a VM, or to start an HA VM, that would leave a member unabsorbed; `off` checks nothing.
"""

from app.core.ha_recovery import LOST_S, _running, memory_mib

POLICIES = ("warn", "strict", "off")
DEFAULT = "warn"
SETTING = "admission_control"


def _store():
    from app.repositories import registry

    return registry.ha().sync


def policy():
    value = _store().settings().get(SETTING)
    return value if value in POLICIES else DEFAULT


def set_policy(value):
    if value not in POLICIES:
        raise ValueError(f"The admission policy is one of: {', '.join(POLICIES)}")
    _store().save_settings({SETTING: value})
    return value


def _cluster():
    """(free memory by online member, running HA VMs' memory by member), this node included."""
    from app.core import cluster_view, ha, self_node, statd

    me = self_node.name()
    latest = statd.latest()
    table = statd.table()
    names = [me, *(m["name"] for m in cluster_view.members())]
    online = [n for n in names if n == me or (n in latest and latest[n][0] <= LOST_S)]
    free = {n: (table.get(n) or {}).get("mem_free_mb") for n in online}
    running = {n: _running(latest[n][1]) if n in latest else set() for n in online}
    loads = {n: [] for n in online}
    for row in ha.list_protected():
        node = me if self_node.is_self(row["node"]) else row["node"]
        if node in loads and row["vm_name"] in running[node]:
            loads[node].append((row["vm_name"], memory_mib(row["domain_xml"] or "")))
    return free, loads


def _absorbed(member, free, loads):
    """True when `member`'s running HA VMs all fit on the other members' free memory; None when some of that memory is
    not known (no member is refused on a guess)."""
    room = {n: f for n, f in free.items() if n != member}
    if any(f is None for f in room.values()):
        return None
    for _, need in sorted(loads[member], key=lambda v: -v[1]):
        if not room:
            return False
        target = max(room, key=room.get)
        if room[target] < need:
            return False
        room[target] -= need
    return True


def check(extra=None):
    """{"policy", "ok", "unabsorbed": [members], "unknown": [members], "detail"}. `extra`: (vm name, node, memory in
    MiB, starting) of a VM about to count as a running HA VM there: HA turned on for it while it runs (its memory is
    already out of its node's free memory), or started (`starting`: it is about to take it)."""
    from app.core import cluster_lead, self_node

    current = policy()
    if current == "off" or not cluster_lead.in_cluster():
        return {"policy": current, "ok": True, "unabsorbed": [], "unknown": [], "detail": ""}
    free, loads = _cluster()
    if extra:
        name, node, need, starting = extra
        node = self_node.name() if self_node.is_self(node) else node
        if node in loads and all(vm != name for vm, _ in loads[node]):
            loads[node].append((name, need))
            if starting and free.get(node) is not None:
                free[node] -= need  # it is about to take that much where it starts
    unabsorbed, unknown = [], []
    for member in loads:
        if not loads[member]:
            continue
        absorbed = _absorbed(member, free, loads)
        if absorbed is None:
            unknown.append(member)
        elif not absorbed:
            unabsorbed.append(member)
    parts = []
    if unabsorbed:
        parts.append(
            f"if {', '.join(unabsorbed)} failed, the other members would not have the memory to restart its HA VMs"
        )
    if unknown:
        parts.append(f"the free memory of some members is not known, so {', '.join(unknown)} could not be checked")
    return {
        "policy": current,
        "ok": not unabsorbed,
        "unabsorbed": unabsorbed,
        "unknown": unknown,
        "detail": "; ".join(parts),
    }


def refuse_if_strict(extra):
    """The check for `extra` about to run as an HA VM: raises RuntimeError under `strict` when it would leave a member
    unabsorbed; returns the result otherwise (the caller says it under `warn`)."""
    result = check(extra)
    if result["policy"] == "strict" and not result["ok"]:
        raise RuntimeError(f"HA admission control (strict): {result['detail']}")
    return result
