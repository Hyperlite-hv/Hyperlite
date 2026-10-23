"""Automatic HA in a cluster (docs/design/ha-automatic.md, section 4, #333 step 4): the leader restarts the HA VMs of a
member that is lost, once that member is known to be stopped.

Every TICK_S, on the leader only and only with the quorum (a node cut from the majority is no leader: it is the one
that fences itself, app/core/ha_selffence.py):

1. A member that carries protected VMs is **lost** when its status (app/core/statd.py, sent every 10 s) is older than
   LOST_S. It was last seen when that status was sent; a leader that never held one (it started since) counts from the
   moment it noticed, which only waits longer.
2. It is **fenced** once its BMC or AMT powered it off and read it off (ha_fencing.power_off), or once its watchdog,
   armed when it was last seen, has had reset_within_s + WATCHDOG_MARGIN_S to reset it. With neither, its VMs go to
   manual.
3. Its VMs are restarted one by one, high priority first: on the first of the VM's preferred nodes that can take it,
   else on the online member with the most free memory enough for it, not in maintenance. A start refused because the
   disk is still locked is tried again until the storage's lease is over (LOCK_WAIT_S, NFSv4: 90 s, plus a margin). At
   most RESTARTS_PER_HOUR automatic restarts of a VM; then it goes to manual.

Each step is in the VM's HA record (its state, replicated: every node shows the same), the audit log and a
notification.
"""

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import libvirt

from app.core.audit import log_action

logger = logging.getLogger(__name__)

TICK_S = 10
LOST_S = 60
WATCHDOG_MARGIN_S = 30
LOCK_WAIT_S = 120
RESTARTS_PER_HOUR = 3
PRIORITY = {"high": 0, "medium": 1, "low": 2}

OK, LOST, FENCING, RECOVERING, MANUAL = "ok", "lost", "fencing", "recovering", "manual"

# member -> {"noticed": monotonic, "last_seen": monotonic, "bmc": result or None, "fenced_at": monotonic or None}:
# the leader's own view, rebuilt from the statuses within a round after it starts.
_lost = {}
_lock = threading.Lock()


def _store():
    from app.repositories import registry

    return registry.ha().sync


def _now():
    return datetime.now(UTC)


def _record(vm_name, state, action):
    """The VM's HA state, written only when it changes: the record is replicated, and a round runs every TICK_S."""
    current = _store().protected_vm(vm_name) or {}
    if current.get("ha_state") == state and (state not in (MANUAL, OK) or current.get("last_action") == action):
        return
    _store().record_watch(vm_name, state, action, _now().isoformat())


def _notify(title, message, result="failed"):
    from app.core.notifications import notify

    notify("ha_alert", title, message, result=result)


def memory_mib(domain_xml):
    """The memory a VM needs, in MiB, from its definition (0 when it cannot be read)."""
    try:
        node = ET.fromstring(domain_xml).find("memory")
        value, unit = int(node.text), (node.get("unit") or "KiB")
    except (ET.ParseError, AttributeError, TypeError, ValueError):
        return 0
    factor = {
        "b": 1 / 2**20,
        "bytes": 1 / 2**20,
        "kib": 1 / 1024,
        "k": 1 / 1024,
        "mib": 1,
        "m": 1,
        "gib": 1024,
        "g": 1024,
    }
    return int(value * factor.get(unit.lower(), 1 / 1024))


def _members():
    from app.core import cluster_view

    return [m["name"] for m in cluster_view.members()]


def _online(ages):
    """Members seen within LOST_S, this node included, with their free memory."""
    from app.core import self_node, statd

    table = statd.table()
    names = [self_node.name(), *(n for n in _members() if ages.get(n) is not None and ages[n] <= LOST_S)]
    return {n: (table.get(n) or {}).get("mem_free_mb") for n in names}


def _in_maintenance(node):
    from app.core import maintenance

    try:
        return bool(maintenance.get(node))
    except Exception:
        logger.debug("Maintenance of %s not read", node, exc_info=True)
        return False


def candidates(row, policy, online, lost):
    """Where the VM may restart, in the order to try: its preferred nodes first, in their order, then the others by
    free memory; never a lost member, one in maintenance, or one without enough free memory."""
    need = memory_mib(row["domain_xml"] or "")
    fits = {n: free for n, free in online.items() if n not in lost and not _in_maintenance(n)}
    fits = {n: free for n, free in fits.items() if free is None or free >= need}
    preferred = [n for n in policy["preferred_nodes"] if n in fits]
    others = sorted((n for n in fits if n not in preferred), key=lambda n: -(fits[n] or 0))
    return preferred + others


def _running_on(member):
    """The VMs `member`'s own status says are running there."""
    from app.core import statd

    vms = (statd.table().get(member) or {}).get("vms") or []
    return {v.get("name") for v in vms if v.get("state") in ("active", "running")}


def _fenced(member, entry):
    """(True, how) once `member` is known to be stopped; (False, why not yet) or (None, why it never will be)."""
    from app.core import ha_fencing, ha_selffence

    method = (ha_fencing.get(member) or {}).get("method")
    if method in ha_fencing.AGENTS:
        if entry.get("bmc") is None:
            entry["bmc"] = ha_fencing.power_off(member)
            log_action(
                "system", "ha_fence", member, "success" if entry["bmc"]["fenced"] else "failed", entry["bmc"]["detail"]
            )
        if entry["bmc"]["fenced"]:
            return True, entry["bmc"]["detail"]
    published = ha_selffence.published(member) or {}
    reset_within = published.get("reset_within_s")
    if published.get("armed") and reset_within:
        waited = time.monotonic() - entry["last_seen"]
        wait = reset_within + WATCHDOG_MARGIN_S
        if waited >= wait:
            return (
                True,
                f"its watchdog was armed and {int(waited)} s passed since it was last seen (reset within {reset_within} s)",
            )
        return False, f"waiting for its watchdog to reset it ({int(wait - waited)} s left)"
    if method in ha_fencing.AGENTS:
        return None, f"its {method} did not confirm it off ({entry['bmc']['detail']}) and its watchdog was not armed"
    return None, "no BMC or AMT, and its watchdog was not armed: nothing proves its VMs stopped"


def _recent_restarts(policy):
    cutoff = _now() - timedelta(hours=1)
    out = []
    for at in policy["auto_restarts"]:
        try:
            if datetime.fromisoformat(at) > cutoff:
                out.append(at)
        except (TypeError, ValueError):
            continue
    return out


LOCKED = re.compile(r"another process using the image|Failed to get .* lock|resource busy|is locked", re.IGNORECASE)


def _restart(row, entry, online, lost):
    """Restart one VM of a fenced member. Returns the node it runs on, or None (state and audit say why)."""
    from app.core import ha

    vm = row["vm_name"]
    policy = _store().policy(vm)
    recent = _recent_restarts(policy)
    if len(recent) >= RESTARTS_PER_HOUR:
        reason = f"{RESTARTS_PER_HOUR} automatic restarts within the last hour: left to an administrator"
        _record(vm, MANUAL, reason)
        log_action("system", "ha_manual", vm, "failed", reason)
        _notify(f"HA: {vm} needs an administrator", reason)
        return None
    targets = candidates(row, policy, online, lost)
    if not targets:
        reason = f"no online member outside maintenance with {memory_mib(row['domain_xml'] or '')} MiB free"
        _record(vm, MANUAL, reason)
        log_action("system", "ha_manual", vm, "failed", reason)
        _notify(f"HA: {vm} needs an administrator", reason)
        return None
    _record(vm, RECOVERING, f"restarting on {targets[0]}")
    last = None
    for target in targets[:2]:  # the first candidate, then the next one once
        try:
            ha.start_on(row, target, reuse_stopped=True)
        except libvirt.libvirtError as e:
            last = f"{target}: {e}"
            locked = LOCKED.search(str(e))
            if locked and time.monotonic() - entry["fenced_at"] < LOCK_WAIT_S:
                _record(vm, RECOVERING, f"its disk is still locked (the old node's lease): trying {target} again")
                return None  # the next round tries again, on the same terms
            ha.forget_on(row, target)
            continue
        except RuntimeError as e:
            last = f"{target}: {e}"
            continue
        _store().record_restart(vm, [*recent, _now().isoformat()])
        _record(vm, OK, f"restarted on {target} after {row['node']} was lost")
        log_action("system", "ha_restarted", vm, "success", f"{row['node']} -> {target}")
        _notify(f"HA: {vm} restarted on {target}", f"{row['node']} was lost and fenced", result="success")
        return target
    reason = f"could not be started on another node ({last})"
    _record(vm, MANUAL, reason)
    log_action("system", "ha_manual", vm, "failed", reason)
    _notify(f"HA: {vm} needs an administrator", reason)
    return None


def tick():
    """One round. Returns {member: state} of the members watched, for the API and the tests."""
    from app.core import cluster_lead, ha, statd

    if not cluster_lead.in_cluster() or not cluster_lead.is_leader() or cluster_lead.read_only_reason():
        with _lock:
            _lost.clear()  # another node acts, or none may: start again from what is seen when this one may
        return {}
    rows = [r for r in ha.list_protected() if r["node"] not in (None, "local")]
    ages = statd.ages()
    members = set(_members())
    watched = sorted({r["node"] for r in rows if r["node"] in members})
    now = time.monotonic()
    states = {}
    with _lock:
        for name in list(_lost):
            if name not in watched:
                del _lost[name]
    for member in watched:
        vms = sorted(
            (r for r in rows if r["node"] == member),
            key=lambda r: (PRIORITY.get(_store().policy(r["vm_name"])["priority"], 1), r.get("enabled_at") or ""),
        )
        age = ages.get(member)
        if age is not None and age <= LOST_S:
            with _lock:
                entry = _lost.get(member)
            if entry is None:
                states[member] = OK
                continue
            if entry["fenced_at"] is None:
                # Back before it was known to be stopped: a network cut its VMs ran through, or a reset (its watchdog)
                # they did not survive. Its own status says which: a VM still running there is left there.
                running = _running_on(member)
                stopped = [r for r in vms if r["vm_name"] not in running]
                for r in vms:
                    if r["vm_name"] in running:
                        _record(r["vm_name"], OK, f"{member} answers again, the VM runs there")
                if not stopped:
                    with _lock:
                        _lost.pop(member, None)
                    log_action("system", "ha_back", member, "success", "the member answers again, its HA VMs run")
                    states[member] = OK
                    continue
                vms = stopped
                entry["fenced_at"] = time.monotonic()
                log_action("system", "ha_fenced", member, "success", "it answers again and its HA VMs do not run there")
            # Known to be stopped: its VMs are restarted even though it answers again (it may be a candidate itself).
            online = _online(ages)
            for r in vms:
                if (_store().protected_vm(r["vm_name"]) or {}).get("ha_state") == MANUAL:
                    continue
                _restart(r, entry, online, set(_lost) - {member})
            states[member] = RECOVERING
            continue
        with _lock:
            entry = _lost.get(member)
            if entry is None:
                entry = _lost[member] = {
                    "noticed": now,
                    "last_seen": now - age if age is not None else now,
                    "bmc": None,
                    "fenced_at": None,
                }
                first = True
            else:
                first = False
        if first:
            for r in vms:
                _record(r["vm_name"], LOST, f"{member} not seen for more than {LOST_S} s")
            log_action("system", "ha_lost", member, "failed", f"no status for more than {LOST_S} s")
            _notify(f"HA: member {member} lost", f"Its HA VMs: {', '.join(r['vm_name'] for r in vms)}")
        if entry["fenced_at"] is None:
            fenced, why = _fenced(member, entry)
            if fenced is None:
                for r in vms:
                    _record(r["vm_name"], MANUAL, f"{member} lost: {why}")
                states[member] = MANUAL
                continue
            if not fenced:
                for r in vms:
                    _record(r["vm_name"], FENCING, f"{member} lost: {why}")
                states[member] = FENCING
                continue
            entry["fenced_at"] = time.monotonic()
            log_action("system", "ha_fenced", member, "success", why)
        online = _online(ages)
        lost = set(_lost)
        for r in vms:
            if (_store().protected_vm(r["vm_name"]) or {}).get("ha_state") == MANUAL:
                continue
            _restart(r, entry, online, lost)
        states[member] = RECOVERING
    return states


def _loop():
    while True:
        try:
            tick()
        except Exception:  # the thread must survive anything, or no VM would ever be restarted again
            logger.exception("HA recovery round failed")
        time.sleep(TICK_S)


def start():
    thread = threading.Thread(target=_loop, daemon=True, name="ha-recovery")
    thread.start()
    return thread
