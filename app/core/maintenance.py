"""Node maintenance mode: the equivalent of vSphere's "Enter maintenance mode" and Proxmox's node maintenance.

A node in maintenance receives no new VM or container, and is never the target of a migration or an HA recovery.
Draining it live-migrates its running VMs, one after another, to a node the admin picked; every migration is its
own task, reusing the ordinary migration job. VMs that cannot move are listed with the reason instead of being
skipped silently:
  - a stopped VM: live migration only moves running VMs, and nothing here copies a stopped one;
  - a VM on iSCSI LUNs (migration refuses them, see iscsi.py);
  - a VM the compatibility diagnostic blocks (CPU, machine type, network...; see cluster_compat.py);
  - a VM whose name already exists on the target.

The node is marked BEFORE its VMs move: otherwise a VM could be created there, or recovered there by HA, while it
is being emptied.

Nodes are named by their label: "local" for the host running Hyperlite (never a row of `nodes`), else the name of
a registered node. Containers are local only and are not drained; they stay where they are.
"""

import logging
from datetime import UTC, datetime

import libvirt
from fastapi import HTTPException

from app.core import cluster_compat, iscsi
from app.core.cluster import get_node
from app.core.database import get_conn

logger = logging.getLogger(__name__)

LOCAL = "local"


def _now():
    return datetime.now(UTC).isoformat()


def label(node):
    """None, "" and "local" all mean the local host."""
    return LOCAL if node in (None, "", LOCAL) else node


def conn_key(node_label):
    """The argument open_conn() takes for a label."""
    return None if node_label == LOCAL else node_label


def check_node_exists(node_label):
    if node_label != LOCAL and not get_node(node_label):
        raise HTTPException(status_code=404, detail=f"Node '{node_label}' not found")


def get(node_label):
    with get_conn() as db:
        row = db.execute("SELECT * FROM node_maintenance WHERE node = ?", (label(node_label),)).fetchone()
    return dict(row) if row else None


def list_all():
    with get_conn() as db:
        rows = db.execute("SELECT * FROM node_maintenance ORDER BY node").fetchall()
    return [dict(r) for r in rows]


def enter(node_label, username):
    """Mark a node in maintenance. Idempotent: the first start time and author are kept."""
    with get_conn() as db:
        db.execute(
            "INSERT INTO node_maintenance (node, started_by, started_at) VALUES (?, ?, ?) ON CONFLICT(node) DO NOTHING",
            (label(node_label), username, _now()),
        )
        db.commit()


def leave(node_label):
    """True when the node was in maintenance."""
    with get_conn() as db:
        cur = db.execute("DELETE FROM node_maintenance WHERE node = ?", (label(node_label),))
        db.commit()
    return cur.rowcount > 0


def refuse_if_in_maintenance(node_label, action):
    """409 for an action that would put a VM or container on a node in maintenance."""
    node_label = label(node_label)
    if get(node_label):
        name = "this host" if node_label == LOCAL else f"node '{node_label}'"
        raise HTTPException(
            status_code=409,
            detail=f"{action} refused: {name} is in maintenance. End the maintenance first, or use another node",
        )


def plan(src_conn, dst_conn, target_node):
    """Which VMs of the source can be live-migrated to `target_node`, and why the others cannot.

    `dst_conn` is None when no target was chosen: then every VM stays. Returns {"migrables": [name...],
    "non_migrables": [{"nom", "raison"}...]}, both sorted by name."""
    migrables, blocked = [], []
    for domain in sorted(src_conn.listAllDomains(0), key=lambda d: d.name()):
        name = domain.name()
        reason = _why_not_migrable(src_conn, domain, dst_conn, target_node)
        if reason:
            blocked.append({"nom": name, "raison": reason})
        else:
            migrables.append(name)
    return {"migrables": migrables, "non_migrables": blocked}


def _why_not_migrable(src_conn, domain, dst_conn, target_node):
    try:
        if not domain.isActive():
            return "Stopped: only running VMs are live-migrated, it stays on this node"
        if iscsi.iscsi_disks_of_domain(domain):
            return "Its disks are iSCSI LUNs, which live migration does not handle yet"
        if dst_conn is None:
            return "No target node chosen: it stays on this node"
        try:
            dst_conn.lookupByName(domain.name())
            return f"A VM with the same name already exists on '{target_node}'"
        except libvirt.libvirtError:
            pass
        compat = cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dst_conn, domain))
        if compat["resume"]["bloquant"]:
            blocking = [c["message"] for c in compat["controles"] if c["statut"] == "blocking"]
            return "Compatibility diagnostic: " + " ; ".join(blocking)
    except libvirt.libvirtError as e:
        logger.warning("Cannot assess %s for migration", domain.name(), exc_info=True)
        return f"Could not be checked: {e.get_error_message()}"
    return None
