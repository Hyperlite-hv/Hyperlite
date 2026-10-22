"""Which other nodes are down, known without asking them: the rule every list and every call to another node follows.

Proxmox VE and vSphere never make a page wait for a host that is gone. Proxmox VE marks a node online or offline from
the cluster's own state (the membership pmxcfs keeps, and the status each node's pvestatd sends), never by probing it
from a request, and its forms refuse a node that is offline. vSphere marks a host "not responding" once its heartbeats
stop, and its lists read vCenter's inventory, never the host. Hyperlite has the same two sources:
- the node poller (app/core/cluster.py), which checks every node and records "offline" in the nodes table;
- the status every member of a cluster sends through hyperlite-cfs (app/core/statd.py).
A node the poller found offline is down, unless its status is current: it is sending, so it is back, whatever the
last poll said. A member whose status alone stopped is not taken for down: its Hyperlite service may be stopped while
the host still runs its VMs and answers libvirt, which a migration off it needs. A node seen down is not asked: a list names it and goes on, a call to it fails at once (NodeDown).

A node switched off seconds ago is not seen down yet: until the poller checks it again, a call to it is bounded by a
short connection timeout (CONNECT_TIMEOUT_S, and scripts/node-ssh for libvirt), never the system's two minutes.
"""

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

OFFLINE = "offline"
CONNECT_TIMEOUT_S = 8


class NodeDown(HTTPException):
    """A call to a node the cluster sees down: refused at once, as Proxmox VE refuses an offline node."""

    def __init__(self, name):
        super().__init__(status_code=503, detail=f"Node '{name}' is offline: it is not reachable")
        self.node = name


def sending():
    """The members whose status is current (app/core/statd.py): alive, whatever the last poll said."""
    from app.core import statd

    try:
        return {name for name, s in statd.table().items() if not s["stale"]}
    except Exception as e:  # no status to read: the poll's word stands
        logger.debug("Statuses not read: %s", e)
        return set()


def down(nodes, current=None):
    """The names, among `nodes` (registered nodes, as cluster.list_nodes() gives them), of those seen down.
    current: the members sending, when the caller already read them."""
    offline = [n["name"] for n in nodes if n.get("status") == OFFLINE]
    if not offline:
        return set()
    current = sending() if current is None else current
    return {name for name in offline if name not in current}


def is_down(node):
    return bool(down([node]))


def check(node):
    """Raise NodeDown for a node seen down."""
    if is_down(node):
        raise NodeDown(node["name"])
