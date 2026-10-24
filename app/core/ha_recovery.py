"""Automatic HA in a cluster (docs/design/ha-automatic.md, section 4, #333 step 4): the leader restarts the HA VMs of a
member that is lost, once that member is known to be stopped.

Every TICK_S, on the leader only and only with the quorum (a node cut from the majority is no leader: it is the one
that fences itself, app/core/ha_selffence.py):

1. A member that carries protected VMs is **lost** when its status (app/core/statd.py, sent every 10 s) is older than
   LOST_S. It was last seen when that status was sent; a leader that never held one (it started since) counts from the
   moment it noticed, which only waits longer.
2. It is **fenced** once its BMC or AMT powered it off and read it off (ha_fencing.power_off, tried BMC_ATTEMPTS
   times, BMC_RETRY_S apart), or once its watchdog, armed when it was last seen, has had reset_within_s +
   WATCHDOG_MARGIN_S to reset it. With neither, its VMs go to manual; so they do, and nothing powers it off, when its
   service published that it stopped normally (an update, a restart: the watchdog is off and its VMs may still run).
3. Its VMs are restarted one by one, high priority first: on the first of the VM's preferred nodes that can take it,
   else on the online member with the most free memory enough for it, not in maintenance. A start refused because the
   disk is still locked is tried again until the storage's lease is over (LOCK_WAIT_S, NFSv4: 90 s, plus a margin). At
   most RESTARTS_PER_HOUR automatic restarts of a VM; then it goes to manual. Each VM started takes its memory out of
   its node's free memory for the next ones, and a VM an earlier round started without its record being updated is
   found where it runs, never started twice.

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
BMC_ATTEMPTS = 3
BMC_RETRY_S = 60
PRIORITY = {"high": 0, "medium": 1, "low": 2}

OK, LOST, FENCING, RECOVERING, MANUAL = "ok", "lost", "fencing", "recovering", "manual"

# member -> {"noticed": monotonic, "last_seen": monotonic, "bmc": result or None, "fenced_at": monotonic or None,
# "recovering": the names of the VMs it was fenced for, restarted elsewhere}:
# the leader's own view, rebuilt from the statuses within a round after it starts.
_lost = {}
_lock = threading.Lock()


def _store():
    from app.repositories import registry

    return registry.ha().sync


def _now():
    return datetime.now(UTC)


def _record(vm_name, state, action):
    """The VM's HA state, written only when it changes: the record is replicated, and a round runs every TICK_S. Each
    change is in the service's log too, where an administrator follows a recovery."""
    current = _store().protected_vm(vm_name) or {}
    if current.get("ha_state") == state and (state not in (MANUAL, OK) or current.get("last_action") == action):
        return
    (logger.warning if state == MANUAL else logger.info)("HA %s: %s (%s)", vm_name, state, action)
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


def _running(vms):
    return {v.get("name") for v in vms if v.get("state") in ("active", "running")}


def _running_on(member):
    """The VMs `member`'s own status says are running there now."""
    from app.core import statd

    return _running((statd.table().get(member) or {}).get("vms") or [])


def _ran_when_lost(member):
    """The VMs running on `member` in its last status before it was lost, or None when none is held (a leader that
    started since): then every HA VM of it counts, as nothing says one of them was stopped on purpose."""
    from app.core import statd

    last = statd.latest().get(member)
    return None if last is None else _running(last[1])


def _fenced(member, entry):
    """(True, how) once `member` is known to be stopped; (False, why not yet) or (None, why it never will be)."""
    from app.core import ha_fencing, ha_selffence

    published = ha_selffence.published(member) or {}
    if published.get("stopped"):
        # Not a failure: its service was stopped normally (an update, a restart) and its VMs may still run there. Never
        # powered off for it, as Proxmox's CRM leaves a node whose LRM stopped cleanly.
        return None, "its Hyperlite service was stopped normally (its watchdog with it): its VMs may still run there"
    method = (ha_fencing.get(member) or {}).get("method")
    if method in ha_fencing.AGENTS:
        tries = entry.setdefault("bmc_tries", 0)
        failed = entry.get("bmc") is not None and not entry["bmc"]["fenced"]
        if entry.get("bmc") is None or (
            failed and tries < BMC_ATTEMPTS and time.monotonic() - entry["bmc_at"] >= BMC_RETRY_S
        ):  # a BMC busy or slow to answer once is asked again, BMC_RETRY_S later
            entry["bmc"] = ha_fencing.power_off(member)
            entry["bmc_tries"], entry["bmc_at"] = tries + 1, time.monotonic()
            log_action(
                "system", "ha_fence", member, "success" if entry["bmc"]["fenced"] else "failed", entry["bmc"]["detail"]
            )
        # The agent's own words go to the audit entry above only: the reasons below reach the service's log, and
        # nothing an agent printed is ever written there.
        if entry["bmc"]["fenced"]:
            return True, f"its {method} powered it off and read it off"
    retry = method in ha_fencing.AGENTS and entry["bmc_tries"] < BMC_ATTEMPTS
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
    if retry:
        return False, f"its {method} did not confirm it off yet: asked again within {BMC_RETRY_S} s"
    if method in ha_fencing.AGENTS:
        return None, f"its {method} did not confirm it off (see the audit log) and its watchdog was not armed"
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


def _may_act():
    """Still the leader, with the quorum: checked again before each restart, as a fence can take minutes."""
    from app.core import cluster_lead

    return cluster_lead.is_leader() and not cluster_lead.read_only_reason()


def _running_elsewhere(row, nodes):
    """The node among `nodes` where this very VM already runs (an earlier round started it there and could not
    update its record after), or None. A node that cannot be asked is said in the log and passed over."""
    from app.core import ha

    for node in nodes:
        try:
            if ha.runs_on(row, node):
                return node
        except Exception as e:
            logger.warning("HA %s: whether it runs on %s is unknown: %s", row["vm_name"], node, e)
    return None


LOCKED = re.compile(r"another process using the image|Failed to get .* lock|resource busy|is locked", re.IGNORECASE)


def _restart(row, entry, online, lost):
    """Restart one VM of a fenced member. Returns the node it runs on, or None (state and audit say why)."""
    from app.core import ha

    vm = row["vm_name"]
    where = _running_elsewhere(row, [n for n in online if n not in lost])
    if where is not None:
        ha.adopt(row, where)
        _record(vm, OK, f"runs on {where}, where an earlier attempt started it after {row['node']} was lost")
        log_action("system", "ha_restarted", vm, "success", f"{row['node']} -> {where} (found running there)")
        return where
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
        logger.info("HA %s: starting it on %s (candidates: %s)", vm, target, ", ".join(targets))
        try:
            ejected = ha.start_on(row, target, reuse_stopped=True)
        except libvirt.libvirtError as e:
            logger.warning("HA %s: not started on %s: %s", vm, target, e)
            last = f"{target}: {e}"
            locked = LOCKED.search(str(e))
            if locked and time.monotonic() - entry["fenced_at"] < LOCK_WAIT_S:
                _record(vm, RECOVERING, f"its disk is still locked (the old node's lease): trying {target} again")
                return None  # the next round tries again, on the same terms
            ha.forget_on(row, target)
            continue
        except Exception as e:  # a conflict, a node not reachable (HTTPException) or a record not written: the next one
            logger.warning("HA %s: not started on %s: %s", vm, target, e)
            last = f"{target}: {e}"
            if not isinstance(e, RuntimeError) and _running_elsewhere(row, [target]):
                # Started there, its record not updated: found again at the next round, never started twice.
                _record(vm, RECOVERING, f"started on {target}, its record not updated yet")
                return None
            continue
        _store().record_restart(vm, [*recent, _now().isoformat()])
        note = f", its CD-ROM emptied ({', '.join(ejected)})" if ejected else ""
        _record(vm, OK, f"restarted on {target} after {row['node']} was lost{note}")
        log_action("system", "ha_restarted", vm, "success", f"{row['node']} -> {target}{note}")
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
                ran = entry["ran"]
                stopped = [r for r in vms if r["vm_name"] not in running and (ran is None or r["vm_name"] in ran)]
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
                entry["recovering"] = {r["vm_name"] for r in stopped}
                log_action("system", "ha_fenced", member, "success", "it answers again and its HA VMs do not run there")
            else:
                # Already known to be stopped: only the VMs it was fenced for are restarted, never one that runs
                # there now, nor one stopped on purpose; once none is left on it, it is back.
                vms = [r for r in vms if r["vm_name"] in entry["recovering"]]
                if not vms:
                    with _lock:
                        _lost.pop(member, None)
                    log_action("system", "ha_back", member, "success", "the member answers again, its HA VMs recovered")
                    states[member] = OK
                    continue
            # Known to be stopped: its VMs are restarted even though it answers again, on another member (as Proxmox
            # VE and vSphere do): whatever made it fail may not be over.
            _restart_all(vms, entry, _online(ages), set(_lost))
            states[member] = RECOVERING
            continue
        with _lock:
            entry = _lost.get(member)
            if entry is None:
                entry = _lost[member] = {
                    "ran": _ran_when_lost(member),
                    "noticed": now,
                    "last_seen": now - age if age is not None else now,
                    "bmc": None,
                    "fenced_at": None,
                }
                first = True
            else:
                first = False
        if entry.get("recovering") is not None:
            vms = [r for r in vms if r["vm_name"] in entry["recovering"]]
        elif entry["ran"] is not None:
            # A VM stopped on purpose before its node was lost stays stopped, on that node, as Proxmox's requested
            # state keeps it.
            idle = [r for r in vms if r["vm_name"] not in entry["ran"]]
            vms = [r for r in vms if r["vm_name"] in entry["ran"]]
            if first:
                for r in idle:
                    _record(r["vm_name"], OK, f"stopped when {member} was lost: not restarted")
        if first:
            for r in vms:
                _record(r["vm_name"], LOST, f"{member} not seen for more than {LOST_S} s")
            logger.warning("HA: member %s lost (last status %s s ago)", member, "unknown" if age is None else int(age))
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
            entry["recovering"] = {r["vm_name"] for r in vms}
            logger.warning("HA: member %s fenced: %s", member, why)
            log_action("system", "ha_fenced", member, "success", why)
        _restart_all(vms, entry, _online(ages), set(_lost))
        states[member] = RECOVERING
    return states


def _restart_all(vms, entry, online, lost):
    """Restart a fenced member's VMs in their order; each one started takes its memory out of its node's free memory
    (read once for the round) for the next ones."""
    for r in vms:
        if not _may_act():
            logger.warning("HA: no longer the leader with the quorum, the next one acts")
            return
        if (_store().protected_vm(r["vm_name"]) or {}).get("ha_state") == MANUAL:
            continue
        target = _restart(r, entry, online, lost)
        if target is not None and online.get(target) is not None:
            online[target] -= memory_mib(r["domain_xml"] or "")


def members_view():
    """Every member as the HA page shows it (#333 step 7): seen within LOST_S or not, its free memory, and what its
    watchdog published (armed, the reset delay, a normal stop). This node first."""
    from app.core import ha_selffence, self_node, statd

    me = self_node.name()
    ages, table = statd.ages(), statd.table()
    out = []
    for name in [me, *_members()]:
        age = ages.get(name)
        published = ha_selffence.published(name) or {}
        out.append(
            {
                "name": name,
                "self": name == me,
                "online": name == me or (age is not None and age <= LOST_S),
                "seen_s": None if age is None else round(age, 1),
                "mem_free_mb": (table.get(name) or {}).get("mem_free_mb"),
                "watchdog": {k: published.get(k) for k in ("armed", "reset_within_s", "stopped", "at")},
            }
        )
    return out


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
