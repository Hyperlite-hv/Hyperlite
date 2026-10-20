"""Who runs what once several nodes run Hyperlite on one replicated configuration (hyperlite-cfs in cluster mode).

Every node is equal, as on Proxmox. Each one runs what concerns the guests it hosts: its scheduled backups, grouped
backups and replication for its own VMs, the automatic deletion of its own VMs, its start at boot. What concerns the
whole cluster runs on one node at a time, the one holding the cfs lock LOCK (the HA watcher, the alerts about a node
that went down): the lock is renewed every RENEW_S, and the daemon releases it when its holder leaves the membership,
or after TTL_S when the holder hangs, so another node takes over.

Outside a cluster (no shadow mode, or the daemon in local mode) this node is alone: it runs everything, as before.
When the daemon does not answer, the last mode it reported holds (kept in app_settings across restarts); a node
that never heard from it behaves as in a cluster, the careful side: it leaves alone what may belong to another node.
"""

import logging
import threading
import time

from app.core import self_node
from app.core.cfs_client import CfsError, Locked

logger = logging.getLogger(__name__)

LOCK = "hyperlite-lead"
MODE_SETTING = "cfs_mode"  # the daemon's last reported mode, a row each node keeps (app/repositories/cfs/tables.py)
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


def _settings():
    from app.repositories.sqlite.settings import SqliteSettingsStore

    return SqliteSettingsStore()


def mode(record=True):
    """The daemon's mode, "local" or "cluster", or None when shadow mode is off or the daemon never answered.
    record=False: never write the setting (a caller inside a write transaction, which that write would wait for)."""
    global _mode, _mode_at
    shadow = _shadow()
    if not shadow.enabled():
        return None
    now = time.monotonic()
    with _lock:
        if _mode is not None and now - _mode_at < STATUS_TTL_S:
            return _mode
    try:
        current = shadow._get_client().status().mode
    except (CfsError, OSError):
        with _lock:
            known = _mode
        return known or _settings().app_setting(MODE_SETTING)
    with _lock:
        changed, _mode, _mode_at = current != _mode, current, now
    if record and changed and _settings().app_setting(MODE_SETTING) != current:
        _settings().set_app_setting(MODE_SETTING, current)
    return current


QUORUM_TTL_S = 2.0
NO_QUORUM = (
    "This node has lost the cluster's quorum: nothing can be changed from it until the quorum is back (the Cluster page "
    "shows the members it reaches)"
)
NO_DAEMON = "The cluster service of this node does not answer: nothing can be changed from it until it does"
_quorum_lock = threading.Lock()
_quorum = (0.0, None)


def read_only_reason():
    """None when this node may change things; else why not, for the person who tried. In a cluster, as on Proxmox, a
    node that lost the quorum (or whose hyperlite-cfs does not answer) changes nothing at all: its configuration would
    diverge from the majority's, and its VMs may be taken over elsewhere. Outside a cluster: always None."""
    global _quorum
    if mode() != "cluster":
        return None
    now = time.monotonic()
    with _quorum_lock:
        at, reason = _quorum
        if now - at < QUORUM_TTL_S:
            return reason
    try:
        reason = None if _shadow()._get_client().status().quorate else NO_QUORUM
    except (CfsError, OSError):
        reason = NO_DAEMON
    if reason is None:
        from app.core import cluster_v1  # imports cluster_view, which imports this module

        reason = cluster_v1.read_only_reason()  # some members still run a version before 1.0
    with _quorum_lock:
        _quorum = (now, reason)
    return reason


def read_only_cause():
    """Why read_only_reason() holds this node, for the dashboard's banner in its own language: ("no_quorum",
    "no_daemon" or "update_to_1_0", the nodes waited for), or (None, []). The reason itself stays the API's text."""
    reason = read_only_reason()
    if reason is None:
        return None, []
    if reason == NO_QUORUM:
        return "no_quorum", []
    if reason == NO_DAEMON:
        return "no_daemon", []
    from app.core import cluster_v1

    return "update_to_1_0", cluster_v1.behind()


_paused = set()


def paused(what):
    """True while this node may change nothing (read_only_reason): the background work named `what` (scheduled
    backups, replication, automatic deletion, start at boot) waits, as on Proxmox. Said once in the log per pause."""
    reason = read_only_reason()
    with _quorum_lock:
        was = what in _paused
        if reason:
            _paused.add(what)
        else:
            _paused.discard(what)
    if reason and not was:
        logger.warning("%s paused: %s", what, reason)
    elif was and not reason:
        logger.info("%s resumed: the cluster's quorum is back", what)
    return bool(reason)


def in_cluster():
    """True when other nodes may run Hyperlite on the same configuration."""
    if not _shadow().enabled():
        return False
    return mode() != "local"


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
    global _mode, _mode_at, _leading_until, _quorum
    with _lock:
        _mode, _mode_at, _leading_until = None, 0.0, 0.0
    with _quorum_lock:
        _quorum = (0.0, None)


def _loop():
    from app.core import cluster_setup

    while True:
        try:
            renew()
        except Exception:  # the thread must survive anything, or no node would run the cluster-wide tasks
            logger.exception("Renewing the cluster lead failed")
        if mode() == "cluster":
            try:
                cluster_setup.sync()  # Corosync's configuration and the members' SSH keys follow the shared ones
            except Exception:
                logger.exception("Following the cluster's configuration failed")
        time.sleep(RENEW_S)


def start_lead_loop():
    threading.Thread(target=_loop, daemon=True, name="cluster-lead").start()
