"""Self-fencing of a node that runs HA VMs (#333, docs/design/ha-automatic.md, option C), as Proxmox does.

Before the leader restarts a failed node's HA VMs elsewhere, it must be sure the old copies are stopped: two copies of a
VM on its shared disk corrupt it. A node that cannot be powered off from outside (no IPMI, Redfish or AMT) fences
itself: while it runs HA VMs in a cluster, it arms its watchdog (/dev/watchdog: a hardware one, or the kernel's
softdog) and feeds it every FEED_S as long as it has the quorum. Cut off from the majority, it stops feeding it, and the
watchdog resets the node TIMEOUT_S later. The leader, which only acts with the quorum, waits for that before
restarting the VMs (step 3).

The watchdog is armed only when needed: in a cluster, while this node hosts a running HA VM. It is disarmed cleanly
(the magic close) when no HA VM runs here any more and when the service stops normally (an update restarts it). A
process that dies or hangs stops feeding it too: then the node is reset, as Proxmox's lrm does, since nothing else
proves its VMs stopped.

Each node publishes whether its watchdog is armed (cfs tree, STATE_DIR/<node>), so the leader knows it may rely on it.
HYPERLITE_WATCHDOG=0 turns all of this off (the node's HA VMs are then recovered by hand only).
"""

import contextlib
import fcntl
import json
import logging
import os
import struct
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core import cluster_lead, self_node
from app.core.cfs_client import CfsError

logger = logging.getLogger(__name__)

TIMEOUT_S = 60
FEED_S = 5
# When the node is reset at the latest after its last feed. Many watchdogs reset at the second expiry, not the first:
# Intel's iTCO (most PCs, and QEMU's q35) reset the lab node 110-120 s after its last feed with a 60 s timeout. The
# leader waits this long, plus its margin, before restarting the node's VMs elsewhere (#333 step 3).
RESET_WITHIN_S = 2 * TIMEOUT_S + FEED_S
STATE_DIR = "/cluster/watchdog"
WDIOC_SETTIMEOUT = 0xC0045706  # _IOWR('W', 6, int), linux/watchdog.h


def device():
    return Path(os.environ.get("HYPERLITE_WATCHDOG_DEVICE") or "/dev/watchdog")


def enabled():
    return os.environ.get("HYPERLITE_WATCHDOG", "1") != "0"


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.fd = None  # the open watchdog: armed
        self.problem = None  # why it could not be armed, for the administrator
        self.starving = False  # armed but not fed: the quorum is lost
        self.stop = threading.Event()


state = _State()


def _hosts_running_ha_vm():
    """True when a VM with HA turned on runs on this node."""
    from app.core import ha
    from app.core.libvirt_utils import open_conn

    mine = [r["vm_name"] for r in ha.list_protected() if r["node"] in (None, "local")]
    if not mine:
        return False
    conn = open_conn()
    try:
        for name in mine:
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:  # not defined here: it does not run here
                continue
            if domain.isActive():
                return True
        return False
    finally:
        conn.close()


def _publish(armed):
    """Tell the cluster whether this node's watchdog guards its HA VMs (read by the leader before a recovery)."""
    from app.repositories.cfs import shadow

    entry = {
        "arme": armed,
        "delai_s": TIMEOUT_S,
        "reinitialise_sous_s": RESET_WITHIN_S,
        "le": datetime.now(UTC).isoformat(),
    }
    path = f"{STATE_DIR}/{shadow.component(self_node.name())}"
    shadow._get_client().put(path, json.dumps(entry).encode())


def published(node):
    """What `node` last published: {"arme", "delai_s", "le"} or None."""
    from app.core.cfs_client import NotFound
    from app.repositories.cfs import shadow

    try:
        return json.loads(shadow._get_client().get(f"{STATE_DIR}/{shadow.component(node)}").data)
    except (NotFound, CfsError, OSError, ValueError):
        return None


def _open():
    path = device()
    if not path.exists() and path == Path("/dev/watchdog"):
        subprocess.run(
            ["modprobe", "softdog", f"soft_margin={TIMEOUT_S}"], capture_output=True, timeout=30, check=False
        )
    fd = os.open(path, os.O_WRONLY)
    with contextlib.suppress(OSError):  # a driver without a settable timeout keeps its own (softdog: soft_margin)
        fcntl.ioctl(fd, WDIOC_SETTIMEOUT, struct.pack("i", TIMEOUT_S))
    return fd


def _feed(fd):
    os.write(fd, b"\0")


def _close(fd):
    """The magic close: the watchdog stops instead of resetting the node."""
    try:
        os.write(fd, b"V")
    finally:
        os.close(fd)


def tick():
    """One round: feed while the quorum holds, then arm or disarm as this node's HA VMs need."""
    if not enabled():
        return
    writable = cluster_lead.read_only_reason() is None
    with state.lock:
        armed = state.fd is not None
        if armed and writable:
            _feed(state.fd)  # first: nothing below (libvirt, the tree) may keep a node with the quorum from feeding it
        if state.starving and writable:
            logger.info("The quorum is back: the watchdog is fed again")
        state.starving = armed and not writable
    if armed and not writable:
        # Cut off from the majority: not fed, the node resets TIMEOUT_S after its last feed. Never disarmed here: this
        # node cannot tell the cluster, and the leader counts on it.
        logger.warning(
            "No quorum: the watchdog is not fed, this node resets within %s s unless it comes back", TIMEOUT_S
        )
        return
    if not writable:
        return
    try:
        needed = cluster_lead.mode() == "cluster" and _hosts_running_ha_vm()
    except Exception as e:  # unknown (libvirt not answering): the watchdog stays as it is
        logger.warning("Whether HA VMs run here is unknown (%s): the watchdog stays as it is", e)
        return
    publish = None
    with state.lock:
        if needed and state.fd is None:
            try:
                state.fd = _open()
            except OSError as e:
                state.problem = f"The watchdog could not be armed ({device()}: {e.strerror})"
                logger.warning("%s: this node's HA VMs can only be recovered by hand", state.problem)
                return
            state.problem = None
            _feed(state.fd)
            logger.info("Watchdog armed: this node runs HA VMs")
            publish = True
        elif not needed and state.fd is not None:
            _close(state.fd)
            state.fd = None
            logger.info("Watchdog disarmed: no HA VM runs here any more")
            publish = False
    if publish is not None:
        try:
            _publish(publish)
        except (CfsError, OSError) as e:
            logger.warning("The watchdog's state could not be published: %s", e)


def stop():
    """The service stops normally (an update, a restart): disarm, so the node is not reset."""
    state.stop.set()
    with state.lock:
        if state.fd is not None:
            _close(state.fd)
            state.fd = None
            logger.info("Watchdog disarmed: the service stops")


def report():
    with state.lock:
        return {"arme": state.fd is not None, "sans_quorum": state.starving, "probleme": state.problem,
                "delai_s": TIMEOUT_S, "reinitialise_sous_s": RESET_WITHIN_S, "actif": enabled()}  # fmt: skip


def _loop():
    while not state.stop.wait(FEED_S):
        try:
            tick()
        except Exception:  # the loop must survive anything; a node that stops feeding resets, which is its purpose
            logger.exception("The watchdog round failed")


def start_selffence():
    threading.Thread(target=_loop, daemon=True, name="ha-watchdog").start()
