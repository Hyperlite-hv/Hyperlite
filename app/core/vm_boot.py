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

from app.core.audit import log_action
from app.core.libvirt_utils import open_conn


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
    """{"demarrage_auto": bool, "ordre": int|None, "delai_s": int} of a VM."""
    row = _store().boot_setting(_node_key(node), vm_name)
    if not row:
        return {"demarrage_auto": False, "ordre": None, "delai_s": 0}
    return {"demarrage_auto": bool(row["autostart"]), "ordre": row["boot_order"], "delai_s": row["delay_s"]}


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
            {"nom": r["vm_name"], "ordre": r["boot_order"], "delai_s": r["delay_s"]}
            for r in _store().autostart_vms(_node_key(node))
        ),
        key=lambda v: (v["ordre"] is None, v["ordre"] or 0, v["nom"]),
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
    conn = open_conn(None if _node_key(node) == LOCAL else node)
    try:
        for step in steps:
            name = step["nom"]
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                logger.warning("Start at boot: VM %s is no longer on %s", name, _node_key(node))
                results.append((name, "absente"))
                continue
            if domain.isActive():
                results.append((name, "deja_active"))
                continue
            try:
                domain.create()
            except libvirt.libvirtError as e:
                logger.error("Start at boot: VM %s did not start on %s: %s", name, _node_key(node), e)
                log_action("system", "autostart_vm", name, "echec", str(e))
                results.append((name, "echec"))
                continue
            log_action("system", "autostart_vm", name, "succes", f"{_node_key(node)}: order {step['ordre']}")
            results.append((name, "demarree"))
            if step["delai_s"]:
                sleep(step["delai_s"])
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


def start_boot_sequence():
    """This host: at service start. Registered nodes: see cluster._poll_nodes (when a node is seen online)."""
    threading.Thread(target=lambda: boot_if_new(LOCAL, _local_boot_id()), daemon=True, name="vm-start-at-boot").start()
