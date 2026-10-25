"""Load balancing of a cluster (docs/design/drs.md, 3.1 to 3.3, #334 step 3): the recommended moves.

The leader reads every member's load from its status (app/core/statd.py, step 1): the node's CPU (its mean over the
last CPU_WINDOW_S, sampled every OBSERVE_S) and memory, as shares of the node, and each VM's. A node's score is the
larger of its CPU and memory shares. The cluster is unbalanced when the spread of the scores (highest minus lowest,
among the members that may receive VMs) stays above the threshold's level for SUSTAINED evaluations in a row, or when
a member stays above HOT_PCT; a VM that breaks a rule is always a reason to move.

The moves, greedy as vSphere's: from the most loaded member, the move of one VM that lowers the spread the most, then
again, MAX_MOVES at most. A VM is a candidate when it runs, all its disks on a shared pool, it is not excluded, it was
not moved in the last COOLDOWN_S, and the target has its memory free with MEM_MARGIN and keeps its rules (no "must"
rule broken, no "should" rule broken by the move). A move is kept only if it lowers the spread by a quarter of the
threshold at least; among equal gains, the VM with the least memory first.

In automatic mode (the default, section 8) the leader applies them (3.5, 3.7, step 4): at most MAX_RUNNING migrations
at a time in the cluster and one per member, never a VM in manual or excluded, each through the live migration of
POST /vms/{name}/migrate (app/routers/vms/migration.py, start), with its checks and its lock. A migration that fails
keeps its VM for COOLDOWN_S; FAILURES_TO_PAUSE failures in a row pause automatic mode until an administrator saves the
settings again, said in a notification. In manual mode, an administrator applies a move from the page.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

OBSERVE_S = 30
EVALUATE_S = 300
CPU_WINDOW_S = 300
SUSTAINED = 3
HOT_PCT = 90.0
MAX_MOVES = 4
MEM_MARGIN = 1.10
COOLDOWN_S = 3600
SPREAD = {1: None, 2: 40.0, 3: 25.0, 4: 15.0, 5: 10.0}  # the threshold's levels, in points of score
MAX_RUNNING = 2
FAILURES_TO_PAUSE = 2

_lock = threading.Lock()
_cpu = {}  # node -> [(monotonic, cpu %)] over CPU_WINDOW_S
_state = {"unbalanced": 0, "at": None, "spread": None, "moves": [], "nodes": {}, "reason": None, "paused": None}
_running = {}  # task id -> the move it carries out
_failures = [0]  # migrations failed in a row
_moved = {}  # vm -> monotonic time of its last move by load balancing


# ---- The cluster's load ----


def observe(table=None, now=None):
    """One sample of every member's CPU, for the means."""
    from app.core import statd

    table = statd.table() if table is None else table
    now = time.monotonic() if now is None else now
    with _lock:
        for node, status in table.items():
            cpu = ((status.get("load") or {}).get("host") or {}).get("cpu_pct")
            if cpu is None or status.get("stale"):
                continue
            samples = [s for s in _cpu.get(node, []) if now - s[0] <= CPU_WINDOW_S]
            samples.append((now, float(cpu)))
            _cpu[node] = samples


def _cpu_mean(node, current):
    with _lock:
        samples = _cpu.get(node) or []
    return sum(c for _, c in samples) / len(samples) if samples else current


def view(table, unavailable=()):
    """{node: {"cpu", "mem", "score", "cpus", "mem_total_mb", "mem_used_mb", "vms": {name: load}}} of the members whose
    status carries a current load; `unavailable`: members that may not receive VMs (maintenance), left out."""
    out = {}
    for node, status in table.items():
        load = status.get("load") or {}
        host = load.get("host") or {}
        total, used, cpu = host.get("mem_total_mb"), host.get("mem_used_mb"), host.get("cpu_pct")
        if status.get("stale") or node in unavailable or not total or used is None or cpu is None:
            continue
        cpu = _cpu_mean(node, float(cpu))
        mem = 100.0 * used / total
        out[node] = {
            "cpu": cpu,
            "mem": mem,
            "score": max(cpu, mem),
            "cpus": host.get("cpus") or 1,
            "mem_total_mb": total,
            "mem_used_mb": used,
            "vms": dict(load.get("vms") or {}),
        }
    return out


def _spread(nodes):
    scores = [n["score"] for n in nodes.values()]
    return (max(scores) - min(scores)) if len(scores) >= 2 else 0.0


def _vm_memory(vm):
    return vm.get("mem_used_mb") or vm.get("mem_total_mb") or 0.0


def _vm_cpu_points(vm, node):
    """The VM's CPU as points of `node`'s CPU: its own use, over its vCPUs, out of the node's CPUs."""
    return (vm.get("cpu_pct") or 0.0) * (vm.get("vcpus") or 1) / (node["cpus"] or 1)


def _after(nodes, vm_name, source, target):
    """`nodes` with `vm_name` moved from `source` to `target` (a copy)."""
    vm = nodes[source]["vms"][vm_name]
    cpu_points = _vm_cpu_points(vm, nodes[source])
    memory = _vm_memory(vm)
    out = {n: {**v, "vms": dict(v["vms"])} for n, v in nodes.items()}
    src, dst = out[source], out[target]
    src["cpu"] -= cpu_points
    src["mem_used_mb"] -= memory
    dst["cpu"] += cpu_points * (src["cpus"] / (dst["cpus"] or 1))
    dst["mem_used_mb"] += memory
    for node in (src, dst):
        node["mem"] = 100.0 * node["mem_used_mb"] / node["mem_total_mb"]
        node["score"] = max(node["cpu"], node["mem"])
    dst["vms"][vm_name] = src["vms"].pop(vm_name)
    return out


def _movable(vm_name, vm, behaviours, now):
    if not vm.get("shared"):  # never a disk copy (section 8)
        return False
    if behaviours.get(vm_name) == "excluded":
        return False
    moved = _moved.get(vm_name)
    return moved is None or now - moved >= COOLDOWN_S


def _fits(vm, target):
    free = target["mem_total_mb"] - target["mem_used_mb"]
    return free >= _vm_memory(vm) * MEM_MARGIN


def _keeps_rules(vm_name, target, where):
    from app.core import drs_rules

    return not drs_rules.broken(vm_name, target, where)


def recommend(nodes, level, behaviours=None, now=None):
    """[{"vm", "source", "target", "gain", "reason"}] of the moves that even the load of `nodes` (view()), at the
    threshold `level` (1 to 5), with the rule fixes first."""
    behaviours = behaviours or {}
    now = time.monotonic() if now is None else now
    where = {vm: node for node, v in nodes.items() for vm in v["vms"]}
    moves = []
    current = nodes
    for move in _rule_fixes(current, where, behaviours, now):
        moves.append(move)
        current = _after(current, move["vm"], move["source"], move["target"])
        where[move["vm"]] = move["target"]
    threshold = SPREAD.get(level)
    while threshold is not None and len(moves) < MAX_MOVES and len(current) >= 2:
        spread = _spread(current)
        source = max(current, key=lambda n: current[n]["score"])
        hot = current[source]["score"] >= HOT_PCT
        if spread < threshold and not hot:
            break
        best = None
        for vm_name, vm in sorted(current[source]["vms"].items()):
            if not _movable(vm_name, vm, behaviours, now) or vm_name in {m["vm"] for m in moves}:
                continue
            for target in current:
                if target == source or not _fits(vm, current[target]) or not _keeps_rules(vm_name, target, where):
                    continue
                after = _after(current, vm_name, source, target)
                gain = spread - _spread(after)
                key = (gain, -_vm_memory(vm))
                if best is None or key > best[0]:
                    best = (key, vm_name, target, after)
        if best is None or best[0][0] < threshold / 4:
            break
        (gain, _), vm_name, target, after = best
        reason = f"{source} at {current[source]['score']:.0f} %" + (
            " (hot)" if hot else f", spread {spread:.0f} points"
        )
        moves.append({"vm": vm_name, "source": source, "target": target, "gain": round(gain, 1), "reason": reason})
        where[vm_name] = target
        current = after
    return moves


def _rule_fixes(nodes, where, behaviours, now):
    """The moves that mend a broken rule: a VM where it breaks one, to a node where it breaks none."""
    from app.core import drs_rules

    out = []
    for vm_name, source in sorted(where.items()):
        vm = nodes[source]["vms"][vm_name]
        broken = drs_rules.broken(vm_name, source, where)
        if not broken or not _movable(vm_name, vm, behaviours, now):
            continue
        targets = [t for t in nodes if t != source and _fits(vm, nodes[t]) and _keeps_rules(vm_name, t, where)]
        if targets:
            target = min(targets, key=lambda t: nodes[t]["score"])
            rule, why = broken[0]
            out.append(
                {
                    "vm": vm_name,
                    "source": source,
                    "target": target,
                    "gain": None,
                    "reason": f"rule '{rule['name']}': {why}",
                }
            )
            where = {**where, vm_name: target}
    return out


# ---- The leader's rounds ----


def evaluate():
    """One evaluation on the leader: the cluster's state and the recommended moves, kept for the API."""
    from app.core import cluster_lead, drs_rules, maintenance, statd

    settings = drs_rules.settings()
    if settings["mode"] == "off" or not cluster_lead.in_cluster():
        with _lock:
            _state.update(unbalanced=0, moves=[], reason="off" if settings["mode"] == "off" else "no cluster")
        return dict(_state)
    from app.core import self_node

    # In maintenance: neither a source to even nor a target ("local" is this node's own row).
    unavailable = {self_node.name() if m["node"] == maintenance.LOCAL else m["node"] for m in maintenance.list_all()}
    nodes = view(statd.table(), unavailable)
    moves = recommend(nodes, settings["threshold"], drs_rules.behaviours())
    threshold = SPREAD.get(settings["threshold"])
    hot = any(n["score"] >= HOT_PCT for n in nodes.values())
    unbalanced = hot or (threshold is not None and _spread(nodes) >= threshold)
    with _lock:
        _state["unbalanced"] = _state["unbalanced"] + 1 if unbalanced else 0
        sustained = _state["unbalanced"] >= SUSTAINED
        rule_moves = [m for m in moves if m["gain"] is None]
        _state.update(
            at=time.time(),
            spread=round(_spread(nodes), 1),
            nodes={
                n: {"cpu": round(v["cpu"], 1), "mem": round(v["mem"], 1), "score": round(v["score"], 1)}
                for n, v in nodes.items()
            },
            moves=moves if sustained else rule_moves,  # a burst is no imbalance; a broken rule is always a reason
            reason=None,
        )
    if settings["mode"] == "automatic":
        _automatic(list(_state["moves"]), drs_rules.behaviours())
    return state()


def state():
    with _lock:
        return {**_state, "moves": list(_state["moves"]), "running": list(_running.values())}


# ---- Applying the moves ----


def _notify(title, message, result="failed"):
    from app.core.notifications import notify

    notify("drs", title, message, result=result)


def apply(move, username):
    """Carry `move` out through the live migration (its checks, the VM's lock, the task). {"task_id", "status"}, or
    the HTTPException the migration raises."""
    from app.core import self_node
    from app.core.audit import log_action
    from app.routers.vms import migration

    me = self_node.name()
    source = None if move["source"] == me else move["source"]
    target = "local" if move["target"] == me else move["target"]
    with _lock:
        _moved[move["vm"]] = time.monotonic()  # tried once: not again for COOLDOWN_S, whatever comes of it
    result = migration.start(move["vm"], source, target, username)
    with _lock:
        _running[result["task_id"]] = move
    detail = f"{move['source']} -> {move['target']}: {move['reason']}"
    log_action(username, "drs_move", move["vm"], "success", detail)
    if username == "drs":
        _notify(f"Load balancing: {move['vm']} moved to {move['target']}", detail, result="success")
    return result


def _settle():
    """The running moves that finished: a success resets the failures, a failure counts."""
    from app.core.tasks import task_status

    with _lock:
        running = dict(_running)
    for task_id, move in running.items():
        status = task_status(task_id)
        if status in (None, "pending", "running"):
            continue
        with _lock:
            _running.pop(task_id, None)
            if status == "succeeded":
                _failures[0] = 0
                continue
            _failures[0] += 1
            paused = _failures[0] >= FAILURES_TO_PAUSE and _state["paused"] is None
            if paused:
                _state["paused"] = f"{_failures[0]} migrations failed in a row (the last: {move['vm']})"
        logger.warning("Load balancing: moving %s to %s failed (%s)", move["vm"], move["target"], status)
        if paused:
            _notify("Load balancing paused", f"{_state['paused']}: save the settings again to resume")


def _automatic(moves, behaviours):
    from fastapi import HTTPException

    _settle()
    with _lock:
        if _state["paused"]:
            return
        busy = {n for m in _running.values() for n in (m["source"], m["target"])}
        room = MAX_RUNNING - len(_running)
    for move in moves:
        if room <= 0:
            break
        if behaviours.get(move["vm"]) == "manual" or busy & {move["source"], move["target"]}:
            continue
        try:
            apply(move, "drs")
        except HTTPException as e:  # refused by the migration's checks: not tried again for COOLDOWN_S
            logger.warning("Load balancing: moving %s to %s refused: %s", move["vm"], move["target"], e.detail)
            continue
        busy |= {move["source"], move["target"]}
        room -= 1


def resume():
    """An administrator saved the settings: automatic mode resumes after a pause."""
    with _lock:
        _state["paused"] = None
        _failures[0] = 0


def _loop():
    from app.core import cluster_lead

    last = 0.0
    while True:
        try:
            if cluster_lead.in_cluster() and cluster_lead.is_leader() and not cluster_lead.read_only_reason():
                observe()
                if time.monotonic() - last >= EVALUATE_S:
                    evaluate()
                    last = time.monotonic()
        except Exception:  # the thread must survive anything
            logger.exception("Load balancing round failed")
        time.sleep(OBSERVE_S)


def start():
    threading.Thread(target=_loop, daemon=True, name="drs").start()
