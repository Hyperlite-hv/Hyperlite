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
at a time in the cluster and one per member, never a VM in manual or excluded. Each move is written in drs_requests and
carried out by the member the VM runs on, the only one that reads the VM's full configuration, through the live
migration of POST /vms/{name}/migrate (app/routers/vms/migration.py, start), with its checks and its lock; it writes
the outcome back. A move keeps its VM for COOLDOWN_S; FAILURES_TO_PAUSE failures in a row pause automatic mode until an
administrator saves the settings again, said in a notification. In manual mode, an administrator applies a move from
the page, sent to that member in the administrator's name.

The same placement serves a drain and a new VM (3.6, step 5): a drained member's VMs each go where they keep their
rules and leave the lowest score, the largest first; a VM created on "auto" goes to the best member that has its
network and storage pool (app/routers/drs.py).
"""

import logging
import os
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# The lab shortens them (HYPERLITE_DRS_*_S): 15 minutes of imbalance before a move is too long for a test.
OBSERVE_S = int(os.environ.get("HYPERLITE_DRS_OBSERVE_S", "30"))
EVALUATE_S = int(os.environ.get("HYPERLITE_DRS_EVALUATE_S", "300"))
CPU_WINDOW_S = int(os.environ.get("HYPERLITE_DRS_CPU_WINDOW_S", "300"))
SUSTAINED = 3
HOT_PCT = 90.0
MAX_MOVES = 4
MEM_MARGIN = 1.10
COOLDOWN_S = 3600
SPREAD = {1: None, 2: 40.0, 3: 25.0, 4: 15.0, 5: 10.0}  # the threshold's levels, in points of score
MAX_RUNNING = 2
FAILURES_TO_PAUSE = 2
PICKUP_S = 300  # a requested move its member has not started by then fails (that member down)
KEEP_FINISHED_S = 86400

_lock = threading.Lock()
_cpu = {}  # node -> [(monotonic, cpu %)] over CPU_WINDOW_S
_state = {"unbalanced": 0, "at": None, "spread": None, "moves": [], "nodes": {}, "reason": None, "paused": None}
_running = {}  # request id (drs_requests) -> the move it asks for, while its member carries it out
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


# ---- Placement: a drained member's VMs and a new VM (3.6, step 5) ----


def place(vm_name, vm, nodes, where):
    """The member of `nodes` (view()) where `vm` (a load) goes: its memory free there with MEM_MARGIN, every rule kept
    (else at least the "must" ones), the lowest score once it holds the VM. None when no member can take it."""
    from app.core import drs_rules

    roomy = [n for n in nodes if _fits(vm, nodes[n])]
    keeping = [n for n in roomy if _keeps_rules(vm_name, n, where)] or [
        n for n in roomy if drs_rules.allowed(vm_name, n, where)
    ]
    if not keeping:
        return None
    return min(keeping, key=lambda n: (_with(nodes, n, vm_name, vm)[n]["score"], n))


def _with(nodes, target, vm_name, vm):
    """`nodes` with `vm` added to `target` (a copy)."""
    out = dict(nodes)
    dst = out[target] = {**nodes[target], "vms": {**nodes[target]["vms"], vm_name: vm}}
    dst["cpu"] += _vm_cpu_points(vm, dst)
    dst["mem_used_mb"] += _vm_memory(vm)
    dst["mem"] = 100.0 * dst["mem_used_mb"] / dst["mem_total_mb"]
    dst["score"] = max(dst["cpu"], dst["mem"])
    return out


def _unavailable():
    """The members in maintenance, by name: neither a source to even nor a target ("local" is this node's own row)."""
    from app.core import maintenance, self_node

    return {self_node.name() if m["node"] == maintenance.LOCAL else m["node"] for m in maintenance.list_all()}


def spread(source, vm_names):
    """{vm: member or None} for draining `source` (a node name): each VM where place() puts it, the largest first, the
    members' loads counting the VMs placed before it."""
    from app.core import drs_rules, statd

    table = statd.table()
    nodes = view(table, _unavailable() | {source})
    loads = ((table.get(source) or {}).get("load") or {}).get("vms") or {}
    where = drs_rules.placement()
    out = {}
    for name in sorted(vm_names, key=lambda n: (-_vm_memory(loads.get(n) or {}), n)):
        vm = loads.get(name) or {}  # its load not read yet: placed on the scores alone
        out[name] = target = place(name, vm, nodes, where)
        if target:
            nodes = _with(nodes, target, name, vm)
            where[name] = target
    return out


def initial(vm_name, memory_mb, vcpus):
    """The members a new VM may go to, best first (3.6): its whole memory counted, no CPU used yet."""
    from app.core import drs_rules, statd

    nodes = view(statd.table(), _unavailable())
    where = drs_rules.placement()
    vm = {"mem_total_mb": float(memory_mb), "vcpus": vcpus, "cpu_pct": 0.0}
    ranked = []
    while (best := place(vm_name, vm, nodes, where)) is not None:
        ranked.append(best)
        nodes = {n: v for n, v in nodes.items() if n != best}
    return ranked


# ---- The leader's rounds ----


def evaluate():
    """One evaluation on the leader: the cluster's state and the recommended moves, kept for the API."""
    from app.core import cluster_lead, drs_rules, statd

    settings = drs_rules.settings()
    if settings["mode"] == "off" or not cluster_lead.in_cluster():
        with _lock:
            _state.update(unbalanced=0, moves=[], reason="off" if settings["mode"] == "off" else "no cluster")
        return dict(_state)
    nodes = view(statd.table(), _unavailable())
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
    """Load balancing's state: this process's, or, in the API beside hyperlite-ha-crm, what it last published."""
    from app import ha_services

    if not ha_services.here(ha_services.CRM):
        from app.core import service_state

        published, _at = service_state.read(PUBLISHED)
        if published is not None:
            return published
    with _lock:
        return {**_state, "moves": list(_state["moves"]), "running": list(_running.values())}


# ---- Applying the moves ----


def _notify(title, message, result="failed"):
    from app.core.notifications import notify

    notify("drs", title, message, result=result)


def apply(move, username):
    """An administrator's move (manual mode, the page's "Apply"): started by the member the VM runs on, through the
    live migration of POST /vms/{name}/migrate (its checks, the VM's lock, the task), in that administrator's name.
    {"task_id", "status"}, or an HTTPException with the migration's refusal."""
    from fastapi import HTTPException

    from app.core import cluster_view, self_node
    from app.core.audit import log_action
    from app.routers.vms import migration

    with _lock:
        _moved[move["vm"]] = time.monotonic()  # tried once: not again for COOLDOWN_S, whatever comes of it
    _cool_down_elsewhere(move["vm"])
    if self_node.is_self(move["source"]):
        result = migration.start(move["vm"], None, move["target"], username)
    else:
        # Only the member the VM runs on reads its full configuration (the checks need it): the request goes there.
        from app.core import rolling_update

        try:
            member = cluster_view.peer(move["source"])
        except LookupError:
            raise HTTPException(status_code=404, detail=f"{move['source']} is not a member of this cluster") from None
        path = f"/vms/{urllib.parse.quote(move['vm'], safe='')}/migrate"
        try:
            status, result = rolling_update.peer_api(member, username, "POST", path, {"target_node": move["target"]})
        except rolling_update.PeerUnreachable as e:
            raise HTTPException(status_code=502, detail=f"{move['source']} does not answer: {e}") from None
        if status != 202:
            status, body = cluster_view.answer(status, result, move["source"])
            raise HTTPException(status_code=status, detail=body.get("detail") if isinstance(body, dict) else body)
    log_action(username, "drs_move", move["vm"], "success", f"{move['source']} -> {move['target']}: {move['reason']}")
    return result


def _cool_down_elsewhere(vm):
    """A manual move's cool-down, for hyperlite-ha-crm, which evaluates (its next round)."""
    from app import ha_services
    from app.core import service_state

    if ha_services.here(ha_services.CRM):
        return
    applied, _at = service_state.read(APPLIED)
    now = time.time()
    recent = {v: t for v, t in (applied or {}).items() if now - t < COOLDOWN_S}
    service_state.publish(APPLIED, {**recent, vm: now})


def _request(move):
    """Automatic mode: the move written for the member the VM runs on, which carries it out (_carry_out)."""
    from app.core import drs_rules

    request_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    drs_rules._store().add_request(
        {
            "id": request_id,
            "vm_name": move["vm"],
            "source": move["source"],
            "target": move["target"],
            "reason": move["reason"],
            "requested_by": "drs",
            "requested_at": now,
        }
    )
    with _lock:
        _moved[move["vm"]] = time.monotonic()
        _running[request_id] = {**move, "at": time.monotonic()}


def _settle():
    """The requested moves that finished: a success resets the failures, a failure counts. A request no member picked
    up within PICKUP_S (its member down) fails."""
    from app.core import drs_rules

    with _lock:
        running = dict(_running)
    for request_id, move in running.items():
        row = drs_rules._store().request(request_id)
        state = row["state"] if row else "failed"
        if state == "pending" and time.monotonic() - move["at"] >= PICKUP_S:
            state = "failed"
            drs_rules._store().update_request(
                request_id, "failed", datetime.now(UTC).isoformat(), detail=f"{move['source']} did not pick it up"
            )
        if state in ("pending", "started"):
            continue
        with _lock:
            _running.pop(request_id, None)
            if state == "succeeded":
                _failures[0] = 0
                continue
            _failures[0] += 1
            paused = _failures[0] >= FAILURES_TO_PAUSE and _state["paused"] is None
            if paused:
                _state["paused"] = f"{_failures[0]} migrations failed in a row (the last: {move['vm']})"
        logger.warning(
            "Load balancing: moving %s to %s failed (%s)", move["vm"], move["target"], (row or {}).get("detail")
        )
        if paused:
            _notify("Load balancing paused", f"{_state['paused']}: save the settings again to resume")


def _automatic(moves, behaviours):
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
        _request(move)
        busy |= {move["source"], move["target"]}
        room -= 1


def _carry_out():
    """On every member: the requested moves of the VMs it runs, started through the live migration in load
    balancing's name, and their outcome written back for the leader."""
    from fastapi import HTTPException

    from app.core import drs_rules, self_node
    from app.core.audit import log_action
    from app.core.tasks import task_status
    from app.routers.vms import migration

    store = drs_rules._store()
    for row in store.requests(source=self_node.name()):
        now = datetime.now(UTC).isoformat()
        vm, detail = row["vm_name"], f"{row['source']} -> {row['target']}: {row['reason']}"
        if row["state"] == "started":
            status = task_status(row["task_id"]) if row["task_id"] else "failed"
            if status in ("succeeded", "failed"):
                store.update_request(row["id"], status, now)
            elif status is None:  # its task is gone (a restart): the migration's outcome is unknown
                store.update_request(row["id"], "failed", now, detail="its task was lost")
            continue
        if row["state"] != "pending":
            continue
        try:
            result = migration.start(vm, None, row["target"], row["requested_by"])
        except HTTPException as e:  # refused by the migration's checks
            store.update_request(row["id"], "failed", now, detail=str(e.detail))
            log_action(row["requested_by"], "drs_move", vm, "failed", f"{detail} ({e.detail})")
            continue
        except Exception as e:
            logger.exception("Load balancing: moving %s failed", vm)
            store.update_request(row["id"], "failed", now, detail=str(e))
            continue
        store.update_request(row["id"], "started", now, task_id=result["task_id"])
        log_action(row["requested_by"], "drs_move", vm, "success", detail)
        _notify(f"Load balancing: {vm} moved to {row['target']}", detail, result="success")


def resume():
    """An administrator saved the settings: automatic mode resumes after a pause. In the API beside hyperlite-ha-crm,
    asked of it (its next round)."""
    from app import ha_services

    if not ha_services.here(ha_services.CRM):
        from app.core import service_state

        service_state.publish(RESUME, {})
        return
    with _lock:
        _state["paused"] = None
        _failures[0] = 0


def _forget_finished():
    from app.core import drs_rules

    drs_rules._store().delete_requests_before((datetime.now(UTC) - timedelta(seconds=KEEP_FINISHED_S)).isoformat())


# ---- Across services (app/ha_services): the moves carried out in hyperlite-ha-lrm, evaluated in hyperlite-ha-crm ----

PUBLISHED = "drs"  # state(), as the CRM last published it (app/core/service_state.py)
RESUME = "drs.resume"  # the API's resume() for the CRM
APPLIED = "drs.applied"  # the API's manual moves, {vm: epoch}, for the CRM's cool-down
_UNSEEN = object()  # no request read yet by this process


def _carry_out_loop():
    from app.core import cluster_lead

    while True:
        try:
            if cluster_lead.in_cluster() and not cluster_lead.read_only_reason():
                _carry_out()
        except Exception:  # the thread must survive anything
            logger.exception("Load balancing: carrying out the moves failed")
        time.sleep(OBSERVE_S)


def _evaluate_loop():
    from app.core import cluster_lead

    last, resumed = 0.0, _UNSEEN
    while True:
        try:
            resumed = _take_requests(resumed)
            if cluster_lead.in_cluster() and not cluster_lead.read_only_reason() and cluster_lead.is_leader():
                observe()
                if time.monotonic() - last >= EVALUATE_S:
                    evaluate()
                    _forget_finished()
                    last = time.monotonic()
            _publish()
        except Exception:  # the thread must survive anything
            logger.exception("Load balancing round failed")
        time.sleep(OBSERVE_S)


def _publish():
    from app import ha_services
    from app.core import service_state

    if ha_services.separate():
        service_state.publish(PUBLISHED, state())


def _take_requests(resumed):
    """What the API asked since the last round: a resume (the time of the last one seen, returned to compare at the
    next round; _UNSEEN at the first), the manual moves' cool-down."""
    from app import ha_services
    from app.core import service_state

    if not ha_services.separate():
        return resumed
    _data, at = service_state.read(RESUME)
    if at != resumed:
        if resumed is not _UNSEEN:  # one already there when this process started is old: only a new one resumes
            resume()
        resumed = at
    applied, _at = service_state.read(APPLIED)
    now_wall, now = time.time(), time.monotonic()
    with _lock:
        for vm, epoch in (applied or {}).items():
            _moved[vm] = max(_moved.get(vm, 0.0), now - (now_wall - epoch))
    return resumed


def start_carry_out():
    """Every node: the moves the leader asked of it (hyperlite-ha-lrm)."""
    threading.Thread(target=_carry_out_loop, daemon=True, name="drs-carry-out").start()


def start_evaluate():
    """Every node, acting on the leader: the cluster's load observed, the moves decided (hyperlite-ha-crm)."""
    threading.Thread(target=_evaluate_loop, daemon=True, name="drs").start()


def start():
    start_carry_out()
    start_evaluate()
