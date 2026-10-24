"""Start at boot: which VMs of a node start when that node itself starts, in which order, and how long to wait
before the next one (a database before the applications that need it).

libvirt's own autostart flag starts every flagged VM at once, in no order, so Hyperlite does it instead and turns
that flag off for the VMs it manages. A node's sequence runs once per boot of that node, never when only the
service restarts or a node's network comes back: a VM an administrator stopped on purpose must not come back
because Hyperlite was updated. The kernel's boot id (read locally, or over SSH for a registered node) tells a real
boot apart. Settings are stored per node and VM, and follow a VM that is live-migrated.
"""

import logging
import threading
import time
from pathlib import Path

import libvirt

from app.core import vm_locks
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.daemon.registry import DaemonError


def _store():
    from app.repositories import registry

    return registry.objects().sync


logger = logging.getLogger(__name__)

BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
MAX_DELAY_S = 3600
MAX_ORDER = 9999
LOCAL = "local"


def _node_key(node):
    return node or LOCAL


def get_setting(vm_name, node=None):
    """{"autostart": bool, "position": int|None, "delay_s": int} of a VM."""
    row = _store().boot_setting(_node_key(node), vm_name)
    if not row:
        return {"autostart": False, "position": None, "delay_s": 0}
    return {"autostart": bool(row["autostart"]), "position": row["boot_order"], "delay_s": row["delay_s"]}


def set_setting(vm_name, autostart, order, delay_s, node=None):
    _store().set_boot_setting(_node_key(node), vm_name, 1 if autostart else 0, order, delay_s)


def delete_setting(vm_name, node=None):
    _store().delete_boot_setting(_node_key(node), vm_name)


def follow_migration(vm_name, source_node, target_node):
    """Move a VM's setting to the node it was live-migrated to; without it, the VM would not start with its new
    node and the old node would look for it in vain. True when there was a setting to move."""
    return _store().move_boot_setting(vm_name, _node_key(source_node), _node_key(target_node))


def sequence(node=None):
    """The VMs of a node to start at boot, in order: by their order number (none last), then by name."""
    return sorted(
        (
            {"name": r["vm_name"], "position": r["boot_order"], "delay_s": r["delay_s"]}
            for r in _store().autostart_vms(_node_key(node))
        ),
        key=lambda v: (v["position"] is None, v["position"] or 0, v["name"]),
    )


def claim_boot(node, boot_id):
    """True the first time this boot of the node is seen, recording it; False afterwards."""
    return _store().claim_boot(_node_key(node), boot_id)


def run_sequence(node=None, sleep=time.sleep):
    """Start the node's VMs of the sequence that are not running, waiting each one's delay before the next. A VM
    that fails to start is logged and the sequence goes on: one broken VM must not keep the others down."""
    steps = sequence(node)
    if not steps:
        return []
    results = []
    conn = open_conn(None if _node_key(node) == LOCAL else node, readonly=True)
    try:
        for step in steps:
            name = step["name"]
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                logger.warning("Start at boot: VM %s is no longer on %s", name, _node_key(node))
                results.append((name, "missing"))
                continue
            if domain.isActive():
                results.append((name, "already_enabled"))
                continue
            from app.core import ha

            if ha.moved_away(name):  # HA restarted it on another member while this one was down
                logger.warning("Start at boot: VM %s runs on another member now (HA), not started here", name)
                results.append((name, "moved"))
                continue
            busy = vm_locks.running(name, None if _node_key(node) == LOCAL else node)
            if busy:  # its disks are being copied or replaced: started meanwhile, it would write to them
                logger.warning("Start at boot: VM %s is busy (%s), not started", name, busy)
                log_action("system", "autostart_vm", name, "failed", f"busy: {busy} in progress")
                results.append((name, "busy"))
                continue
            try:
                # Started by hyperlite-daemon (app/daemon/ops/vm.py), here or on the node registered over SSH only.
                from app.core import privileged

                remote = None if _node_key(node) == LOCAL else node
                privileged.run("vm.power", user="system", vm=name, action="start", node=remote)
            except (libvirt.libvirtError, DaemonError) as e:
                reason = e.detail if isinstance(e, DaemonError) else str(e)
                logger.error("Start at boot: VM %s did not start on %s: %s", name, _node_key(node), reason)
                log_action("system", "autostart_vm", name, "failed", reason)
                results.append((name, "failed"))
                continue
            log_action("system", "autostart_vm", name, "success", f"{_node_key(node)}: order {step['position']}")
            results.append((name, "started"))
            if step["delay_s"]:
                sleep(step["delay_s"])
    finally:
        conn.close()
    return results


def _local_boot_id():
    try:
        return BOOT_ID_FILE.read_text().strip() or None
    except OSError as e:
        logger.warning("Cannot read the boot id of this host (%s): start at boot is skipped", e)
        return None


def remote_boot_id(node_row):
    """The boot id of a registered node, read over the cluster's SSH trust; None when unreachable."""
    from app.core.cluster import _run_ssh  # late import: cluster.py imports much of the core

    try:
        r = _run_ssh(node_row, ["cat", str(BOOT_ID_FILE)], timeout=10)
    except (OSError, ValueError) as e:
        logger.warning("Cannot read the boot id of %s: %s", node_row["name"], e)
        return None
    if r.returncode != 0:
        logger.warning("Cannot read the boot id of %s: %s", node_row["name"], r.stderr.strip())
        return None
    return r.stdout.strip() or None


def boot_if_new(node, boot_id):
    """Run the node's sequence when this boot id was never seen for it."""
    if not boot_id or not claim_boot(node, boot_id):
        return []
    try:
        return run_sequence(node)
    except libvirt.libvirtError as e:
        logger.error("Start at boot on %s could not reach libvirt: %s", _node_key(node), e)
        return []


QUORUM_WAIT_S = 10


def _boot_when_writable():
    # In a cluster, a node that just started often has no quorum yet: its VMs start once it has (as Proxmox's
    # pve-guests waits for it), never on a node cut off from the others. The boot id is claimed only then.
    from app.core import cluster_lead

    while cluster_lead.paused("Start at boot"):
        time.sleep(QUORUM_WAIT_S)
    from app.core import ha

    try:
        ha.forget_moved()  # before anything starts: the VMs HA moved while this node was down
    except Exception:
        logger.exception("The VMs HA moved away could not be undefined: they are not started here anyway")
    boot_if_new(LOCAL, _local_boot_id())


def start_boot_sequence():
    """This host: at service start. Registered nodes: see cluster._poll_nodes (when a node is seen online)."""
    threading.Thread(target=_boot_when_writable, daemon=True, name="vm-start-at-boot").start()
