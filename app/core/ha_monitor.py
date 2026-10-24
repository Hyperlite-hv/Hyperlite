"""VM monitoring of HA VMs (docs/design/ha-automatic.md, 4.9, #333 step 6), as vSphere's: a guest that hangs is reset
in place by its own node.

Every node watches, every ROUND_S, the HA VMs running on it whose monitoring is on (off by default, per VM). A round
asks each one's guest agent `guest-ping`. A VM is reset (`virsh reset`) once all of these hold:

- its agent answered since the VM started, or since its last reset: a VM without the agent, or whose agent never
  came up, is never reset (the option says it needs qemu-guest-agent in the guest);
- its agent channel is still connected and did not answer for MISSED_ROUNDS rounds in a row: an agent stopped in
  the guest disconnects its channel, and is no hang;
- the VM read and wrote nothing on its disks and its network cards over those rounds, as vSphere's I/O check: a
  busy guest whose agent is slow keeps doing I/O;
- it runs (not paused, not migrating, no libvirt job and no Hyperlite operation on it), this node is not in
  maintenance and has the quorum.

At most RESETS_PER_HOUR resets of a VM within an hour; then it is left to an administrator, in its HA record and a
notification. It is the node's own work, with no failover: the VM stays where it is.
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import libvirt

from app.core.audit import log_action

logger = logging.getLogger(__name__)

ROUND_S = 30
MISSED_ROUNDS = 3
RESETS_PER_HOUR = 3
PING_TIMEOUT_S = 5

# domain UUID -> {"id": the libvirt id of this run, "answered": bool, "missed": int, "io": counters or None}: what
# this node saw of each VM it watches, since the VM's current run.
_seen = {}


def _store():
    from app.repositories import registry

    return registry.ha().sync


def _now():
    return datetime.now(UTC)


def _notify(title, message, result="failed"):
    from app.core.notifications import notify

    notify("ha_alert", title, message, result=result)


def _ping(domain):
    """True when the guest agent answered guest-ping, False when it did not; None when its channel is not connected
    (no agent, or one stopped in the guest): that says nothing of a hang."""
    import libvirt_qemu

    from app.core import guest_agent

    if guest_agent.state(domain) != guest_agent.CONNECTED:
        return None
    try:
        libvirt_qemu.qemuAgentCommand(domain, '{"execute":"guest-ping"}', PING_TIMEOUT_S, 0)
        return True
    except libvirt.libvirtError:
        return False


def _io(domain):
    """The VM's disk and network counters, summed: they move while the guest works."""
    root = ET.fromstring(domain.XMLDesc(0))
    total = [0, 0, 0, 0]
    for target in root.findall("./devices/disk/target"):
        try:
            rd_req, rd_bytes, wr_req, wr_bytes, _ = domain.blockStats(target.get("dev"))
        except libvirt.libvirtError:
            continue
        total[0] += rd_req + wr_req
        total[1] += rd_bytes + wr_bytes
    for target in root.findall("./devices/interface/target"):
        try:
            stats = domain.interfaceStats(target.get("dev"))
        except libvirt.libvirtError:
            continue
        total[2] += stats[1] + stats[5]  # packets received, sent
        total[3] += stats[0] + stats[4]  # bytes received, sent
    return tuple(total)


def _quiet(domain):
    """Why the VM may not be reset now, or None: it must run with nothing else working on it."""
    from app.core import vm_locks

    if domain.state()[0] != libvirt.VIR_DOMAIN_RUNNING:
        return "not running"
    if domain.jobInfo()[0] != libvirt.VIR_DOMAIN_JOB_NONE:  # a migration, a save, a dump
        return "a libvirt job runs on it"
    if vm_locks.running(domain.name()):
        return f"{vm_locks.running(domain.name())} runs on it"
    return None


def _recent(resets):
    cutoff = _now() - timedelta(hours=1)
    out = []
    for at in resets:
        try:
            if datetime.fromisoformat(at) > cutoff:
                out.append(at)
        except (TypeError, ValueError):
            continue
    return out


def _record(vm_name, action):
    """What monitoring did, in the VM's HA record: its HA state stays the leader's (a VM left to an administrator here
    must still be restarted elsewhere if its node is lost)."""
    state = (_store().protected_vm(vm_name) or {}).get("ha_state") or "ok"
    _store().record_watch(vm_name, state, action, _now().isoformat())


def _reset(domain, seen):
    vm = domain.name()
    settings = _store().monitoring(vm)
    recent = _recent(settings["resets"])
    if len(recent) >= RESETS_PER_HOUR:
        if seen.get("left") is None:
            reason = f"its guest agent stopped answering again after {RESETS_PER_HOUR} resets within the hour"
            seen["left"] = True
            _record(vm, f"VM monitoring: {reason}: left to an administrator")
            log_action("system", "ha_monitor_manual", vm, "failed", reason)
            _notify(f"HA: {vm} needs an administrator", reason)
        return
    domain.reset(0)
    _store().record_reset(vm, [*recent, _now().isoformat()])
    why = f"its guest agent did not answer for {MISSED_ROUNDS * ROUND_S} s and it did no disk or network I/O"
    _record(vm, f"VM monitoring: reset, {why}")
    log_action("system", "ha_monitor_reset", vm, "success", why)
    _notify(f"HA: {vm} reset", why, result="success")
    logger.warning("HA monitoring: %s reset (%s)", vm, why)
    seen.update(answered=False, missed=0, io=None)  # its agent must answer again before it is watched again


def watch(domain, seen):
    """One round for one VM: `seen` is what this node knows of its current run, updated here."""
    answer = _ping(domain)
    if answer:
        seen.update(answered=True, missed=0, io=None, left=None)
        return
    if not seen["answered"] or answer is None:
        seen.update(missed=0, io=None)  # no agent to count on, or one stopped on purpose
        return
    if _quiet(domain) is not None:
        seen.update(missed=0, io=None)
        return
    io = _io(domain)
    if seen["missed"] == 0 or io != seen["io"]:
        # The first silent round, or I/O since the last one: counting starts (again) from here.
        seen.update(missed=1, io=io)
        return
    seen["missed"] += 1
    if seen["missed"] >= MISSED_ROUNDS:
        _reset(domain, seen)


def tick():
    """One round over this node's running HA VMs whose monitoring is on."""
    from app.core import cluster_lead, ha, maintenance, self_node
    from app.core.libvirt_utils import open_conn

    if cluster_lead.in_cluster() and cluster_lead.read_only_reason():
        return  # without the quorum, nothing is recorded: and its watchdog acts for the node
    if maintenance.get(maintenance.LOCAL):
        return
    names = [
        r["vm_name"]
        for r in ha.list_protected()
        if self_node.is_self(r["node"]) and _store().monitoring(r["vm_name"])["enabled"]
    ]
    if not names:
        _seen.clear()
        return
    conn = open_conn()
    try:
        alive = set()
        for name in names:
            try:
                domain = conn.lookupByName(name)
                if not domain.isActive():
                    continue
                uuid, run = domain.UUIDString(), domain.ID()
            except libvirt.libvirtError:
                continue
            alive.add(uuid)
            seen = _seen.get(uuid)
            if seen is None or seen["id"] != run:  # first seen, or started again since
                seen = _seen[uuid] = {"id": run, "answered": False, "missed": 0, "io": None, "left": None}
            try:
                watch(domain, seen)
            except libvirt.libvirtError as e:
                logger.warning("HA monitoring of %s: %s", name, e)
        for uuid in list(_seen):
            if uuid not in alive:
                del _seen[uuid]
    finally:
        conn.close()


def _loop():
    while True:
        try:
            tick()
        except Exception:  # the thread must survive anything, or no VM would be watched again
            logger.exception("HA monitoring round failed")
        time.sleep(ROUND_S)


def start():
    threading.Thread(target=_loop, daemon=True, name="ha-monitor").start()
