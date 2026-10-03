"""Tells when a VM of this host stops by accident.

libvirt keeps the reason a VM is in its state: a QEMU process that died (a segfault, the kernel's out-of-memory
killer) leaves the VM shut off for the reason "crashed" or "failed", and a guest that panics with a pvpanic device
puts it in the "crashed" state. Neither was told to anyone: the dashboard only showed the VM as stopped (or crashed)
the next time someone looked. Every node watches its own VMs; a change of state into one of those is audited as
"vm_crashed", which a notification channel can subscribe to.

A shutdown from the guest, a stop or forced stop from Hyperlite, a migration or a saved state are not crashes.
"""

import logging
import threading
import time

import libvirt

logger = logging.getLogger(__name__)

WATCH_INTERVAL_S = 15

_ACCIDENTS = {
    (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_CRASHED): "its QEMU process stopped unexpectedly",
    (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_FAILED): "it failed to run (QEMU error)",
}

# uuid -> (state, reason) at the previous round; None before the first one, which only records what is there.
_last = None


def _why(state, reason):
    if state == libvirt.VIR_DOMAIN_CRASHED:
        return "the guest crashed (kernel panic)" if reason == libvirt.VIR_DOMAIN_CRASHED_PANICKED else "it crashed"
    return _ACCIDENTS.get((state, reason))


def tick(conn):
    """One round over this host's VMs: the crashes since the previous round, as [(name, why)], audited."""
    from app.core.audit import log_action

    global _last
    now = {}
    crashes = []
    for domain in conn.listAllDomains():
        try:
            uuid, name = domain.UUIDString(), domain.name()
            state, reason = domain.state()
        except libvirt.libvirtError:
            continue  # deleted during the round
        now[uuid] = (state, reason)
        before = _last.get(uuid) if _last is not None else None
        why = _why(state, reason)
        # Only a change: a VM found crashed at start, or still crashed, was already told (or predates this process).
        # Shut off by accident means it was running: a start that fails also leaves "failed", and is answered then.
        was_running = before is not None and before[0] != libvirt.VIR_DOMAIN_SHUTOFF
        if (
            why
            and before is not None
            and before != (state, reason)
            and (state == libvirt.VIR_DOMAIN_CRASHED or was_running)
        ):
            crashes.append((name, why))
            log_action("system", "vm_crashed", name, "echec", why)
    _last = now
    return crashes


def _loop():
    from app.core.libvirt_utils import open_conn

    while True:
        try:
            conn = open_conn()
            try:
                tick(conn)
            finally:
                conn.close()
        except Exception:
            logger.exception("VM crash watch round failed")
        time.sleep(WATCH_INTERVAL_S)


def forget():
    global _last
    _last = None


def start_vm_crash_watch():
    thread = threading.Thread(target=_loop, daemon=True, name="vm-crash-watch")
    thread.start()
    return thread
