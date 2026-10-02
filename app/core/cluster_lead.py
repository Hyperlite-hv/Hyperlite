"""Who runs what once several nodes run Hyperlite on one replicated configuration (hyperlite-cfs in cluster mode).

Every node is equal, as on Proxmox. Each one runs what concerns the guests it hosts: its scheduled backups, grouped
backups and replication for its own VMs, the automatic deletion of its own VMs, its start at boot. What concerns the
whole cluster runs on one node at a time, the one holding the cfs lock LOCK (the HA watcher, the alerts about a node
that went down): the lock is renewed every RENEW_S, and the daemon releases it when its holder leaves the membership,
or after TTL_S when the holder hangs, so another node takes over.

Outside a cluster (no shadow mode, or the daemon in local mode) this node is alone: it runs everything, as before.
When the daemon does not answer, the last mode it reported holds; before any answer, a node with shadow mode on
behaves as in a cluster, the careful side: it leaves alone what may belong to another node.
"""

import logging
import threading
import time

from app.core import self_node
from app.core.cfs_client import CfsError, Locked

logger = logging.getLogger(__name__)

LOCK = "hyperlite-lead"
TTL_S = 60
RENEW_S = 15
STATUS_TTL_S = 5.0

_lock = threading.Lock()
_mode = None  # the daemon's last reported mode
_mode_at = 0.0
_leading_until = 0.0


def _shadow():
    from app.repositories.cfs import shadow

    return shadow


def in_cluster():
    """True when other nodes may run Hyperlite on the same configuration."""
    global _mode, _mode_at
    shadow = _shadow()
    if not shadow.enabled():
        return False
    now = time.monotonic()
    with _lock:
        fresh = _mode is not None and now - _mode_at < STATUS_TTL_S
        if fresh:
            return _mode != "local"
    try:
        mode = shadow._get_client().status().mode
    except (CfsError, OSError):
        with _lock:
            return _mode != "local"
    with _lock:
        _mode, _mode_at = mode, now
    return mode != "local"


def is_leader():
    """True when this node runs the cluster-wide work: always outside a cluster, else while it holds the lock."""
    if not in_cluster():
        return True
    with _lock:
        return time.monotonic() < _leading_until


def renew():
    """Take or renew the lock. The holder is named after the node, so a node that restarts gets its own lock back."""
    global _leading_until
    if not in_cluster():
        with _lock:
            _leading_until = 0.0
        return False
    try:
        _shadow()._get_client().lock(LOCK, self_node.name(), TTL_S)
        held = True
    except Locked:
        held = False
    except (CfsError, OSError) as e:  # no quorum, daemon down: another node may hold it by now
        logger.debug("cluster lead not renewed: %s", e)
        held = False
    with _lock:
        was = time.monotonic() < _leading_until
        # Valid a little less than the lock itself, so this node stops before the daemon could give it to another.
        _leading_until = time.monotonic() + TTL_S - 2 * RENEW_S if held else 0.0
    if held != was:
        logger.info("This node %s the cluster-wide tasks", "now runs" if held else "no longer runs")
    return held


def forget():
    """Drop what is known of the daemon (tests, and after shadow mode was turned on or off)."""
    global _mode, _mode_at, _leading_until
    with _lock:
        _mode, _mode_at, _leading_until = None, 0.0, 0.0


def _loop():
    while True:
        try:
            renew()
        except Exception:  # the thread must survive anything, or no node would run the cluster-wide tasks
            logger.exception("Renewing the cluster lead failed")
        time.sleep(RENEW_S)


def start_lead_loop():
    threading.Thread(target=_loop, daemon=True, name="cluster-lead").start()
