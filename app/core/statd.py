"""The cluster's status (phase E, docs/design/phase-e-statd.md): every node sends a summary of its VMs to every node,
and the lists read it here instead of asking each node on each request (docs/scale-results.md).

Every PERIOD_S, and at once after a change made through Hyperlite (inventory_cache.invalidate), a node in a cluster
reads its own VMs (the inventory scan the VM list already uses: one bulk libvirt call) and sends the summary through
hyperlite-cfs's status group, compressed. Every node keeps every node's latest summary in memory (the daemon), with
its age: up to STALE_S it is current, up to GONE_S it is shown as stale, beyond it the node counts as not answering. A
change made outside Hyperlite (a guest shutting itself down) shows within PERIOD_S.

Outside a cluster nothing is sent: the lists read this node directly, as before.
"""

import json
import logging
import threading
import time
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

PERIOD_S = 10
STALE_S = 30
GONE_S = 90
MEMINFO = Path("/proc/meminfo")
DEBOUNCE_S = 0.5  # several changes in a row (a VM created then started): one summary

_wake = threading.Event()


def changed():
    """Something changed through Hyperlite: send this node's summary soon instead of at the next period."""
    _wake.set()


def _in_cluster():
    from app.core import cluster_lead

    return cluster_lead.mode() == "cluster"


def summary():
    """This node's VMs, as the VM list shows them, with the node's name and the time they were read."""
    from app.core import self_node
    from app.core.libvirt_utils import open_conn
    from app.routers.vms._shared import inventory

    conn = open_conn()
    try:
        vms = inventory(conn)["vms"]
    finally:
        conn.close()
    from app.core import cluster_v1

    # "api": the version of the API and of the names this node writes: the others know when every member runs 1.0.
    # "features": what this node's database accepts beyond that, so a change the others' could not store waits until
    # every member runs a version that knows it (members_without).
    return {
        "node": self_node.name(),
        "at": time.time(),
        "api": cluster_v1.API_VERSION,
        "features": list(FEATURES),
        "mem_free_mb": _mem_free_mb(),
        "vms": vms,
    }


def _mem_free_mb():
    """The memory a new VM could take here (MemAvailable), for HA's placement (docs/design/ha-automatic.md, 4.3)."""
    try:
        for line in MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        logger.debug("MemAvailable not read", exc_info=True)
    return None


def encode(data):
    return zlib.compress(json.dumps(data, separators=(",", ":")).encode(), 6)


def publish():
    """Send this node's summary to every node. Returns the size sent, or None when it could not be."""
    from app.core.cfs_client import STAT_MAX, CfsError
    from app.repositories.cfs import shadow

    blob = encode(summary())
    if len(blob) > STAT_MAX:
        logger.warning(
            "This node's status is %d bytes, over %d: not sent; the lists ask it directly", len(blob), STAT_MAX
        )
        return None
    try:
        shadow._get_client().stat_put(blob)
    except (CfsError, OSError) as e:
        logger.debug("Status not sent: %s", e)
        return None
    return len(blob)


def table():
    """{node name: {"vms": [...], "age_s": float, "stale": bool}} of every node whose summary is under GONE_S old, this
    node's included. Empty when hyperlite-cfs does not answer (the lists then ask each node, as before)."""
    from app.core.cfs_client import CfsError
    from app.repositories.cfs import shadow

    try:
        statuses = shadow._get_client().stat_get()
    except (CfsError, OSError) as e:
        logger.debug("Statuses not read: %s", e)
        return {}
    out = {}
    for s in statuses:
        age = s.age_ms / 1000
        if age > GONE_S:
            continue
        try:
            data = json.loads(zlib.decompress(s.data))
            name, vms = data["node"], data["vms"]
        except (ValueError, KeyError, TypeError, zlib.error):
            logger.warning("A status that could not be read (node id %s)", s.node)
            continue
        out[name] = {
            "vms": vms,
            "age_s": round(age, 1),
            "stale": age > STALE_S,
            "api": data.get("api", 0),
            "features": data.get("features", []),
            "mem_free_mb": data.get("mem_free_mb"),
        }
    return out


def latest():
    """{node name: (age in seconds, its VMs)} of the latest status of every node the daemon holds one of, however old:
    HA counts from the moment a node was last seen, and restarts what ran there then (app/core/ha_recovery.py). Empty
    when hyperlite-cfs does not answer."""
    from app.core.cfs_client import CfsError
    from app.repositories.cfs import shadow

    try:
        statuses = shadow._get_client().stat_get()
    except (CfsError, OSError) as e:
        logger.debug("Statuses not read: %s", e)
        return {}
    out = {}
    for s in statuses:
        try:
            data = json.loads(zlib.decompress(s.data))
            out[data["node"]] = (s.age_ms / 1000, data.get("vms") or [])
        except (ValueError, KeyError, TypeError, zlib.error):
            logger.warning("A status that could not be read (node id %s)", s.node)
    return out


def ages():
    """{node name: age in seconds of its latest status}, however old (latest)."""
    return {name: age for name, (age, _) in latest().items()}


# What this node's database accepts that an older version did not: an assignment on a storage pool or a network, then
# on a backup store (app/core/permissions.py, RESOURCE_TYPES).
FEATURES = ("acl_storage_network", "acl_backup_store")


def members_without(feature):
    """The other members of the cluster that do not say they know `feature`, by name; empty outside a cluster. A member
    whose status is not read (down, or before this field) counts as not knowing it: a row it cannot store would stop
    every change from reaching it (app/repositories/cfs/inbound.py refuses the whole apply)."""
    from app.core import cluster_view

    known = table()
    return sorted(
        m["name"] for m in cluster_view.members() if feature not in known.get(m["name"], {}).get("features", ())
    )


def _loop(stop):
    while not stop.is_set():
        _wake.wait(PERIOD_S)
        if stop.is_set():
            return
        if _wake.is_set():
            time.sleep(DEBOUNCE_S)
        _wake.clear()
        try:
            if _in_cluster():
                publish()
        except Exception:  # the loop must survive anything; the lists then ask the nodes directly
            logger.exception("The status round failed")


def start():
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True, name="statd").start()
    _wake.set()  # a first summary at once
    return stop
