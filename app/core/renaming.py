"""Renaming a VM, a container or a node: what Hyperlite keeps under the old name follows it.

libvirt knows a VM or a container by its name, and so does Hyperlite's database: its SSH user, backups and their
schedule, pools and permissions, HA protection, start at boot, notes and tags, metrics history... A rename moves all
of it in one transaction, so the object keeps its settings and its history. The audit log and finished tasks are
history and keep the name the object had then.

A node is only a name in this database (the machine itself keeps its host name): the rename moves the rows that name
it, and the name of the key it uses to reach this host back for migrations (app/core/cluster.py::ensure_reverse_trust).
"""

import json
import logging
import xml.etree.ElementTree as ET

from app.core.database import get_conn

logger = logging.getLogger(__name__)

LOCAL = "local"

# (table, column) holding a VM's name, whatever its node. UPDATE OR REPLACE: a row left behind under the new name by
# an object deleted long ago must not block the rename (the renamed VM's own settings win).
_VM_COLUMNS = (
    ("vm_ssh_users", "vm_name"),
    ("vm_provisioning", "vm_name"),
    ("vm_os_label", "vm_name"),
    ("vm_auto_cleanup", "vm_name"),
    ("vm_cloudinit", "vm_name"),
    ("backup_jobs", "vm_name"),
    ("backups", "vm_name"),
    ("pool_members", "vm_name"),
    ("ha_protected_vms", "vm_name"),
)

_CONTAINER_COLUMNS = (
    ("container_ssh_users", "container_name"),
    ("container_storage", "container_name"),
    ("container_apps", "container_name"),
    ("container_backups", "container_name"),
)

# (table, column) holding a node's name ("local" for this host, which cannot be renamed here).
_NODE_COLUMNS = (
    ("config_copies", "node"),
    ("node_fencing", "node"),
    ("node_maintenance", "node"),
    ("node_live", "name"),
    ("storage_samples", "node"),
    ("vm_boot", "node"),
    ("vm_boot_state", "node"),
    ("ha_protected_vms", "node"),
    ("tasks", "node"),
)


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _move(db, tables, table, column, old, new, extra="", params=()):
    if table not in tables:
        return
    db.execute(
        f"UPDATE OR REPLACE {table} SET {column} = ? WHERE {column} = ?{extra}",  # noqa: S608 - fixed names above
        (new, old, *params),
    )


def _renamed_xml(xml, new):
    """HA's cached copy of a VM's definition (app/core/ha.py), with the new name: it is what would redefine the VM on
    another node, and the periodic refresh may not have run yet."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    name = root.find("name")
    if name is not None:
        name.text = new
    return ET.tostring(root, encoding="unicode")


def vm_records(old, new, node=None):
    """Move a VM's records from `old` to `new`."""
    node_key = node or LOCAL
    with get_conn() as db:
        tables = _tables(db)
        for table, column in _VM_COLUMNS:
            _move(db, tables, table, column, old, new)
        _move(db, tables, "acl", "resource_id", old, new, " AND resource_type = 'vm'")
        _move(db, tables, "vm_boot", "vm_name", old, new, " AND node = ?", (node_key,))
        _move(db, tables, "object_meta", "name", old, new, " AND kind = 'vm' AND node = ?", (node_key,))
        _move(db, tables, "job_steps", "cible", old, new, " AND cible_type = 'vm'")
        # Metrics: a local VM is sampled as "<vm>", a VM of a registered node as "<node>:<vm>" (app/core/metrics.py).
        prefix = f"{node}:" if node and node != LOCAL else ""
        _move(db, tables, "metrics_samples", "cible", prefix + old, prefix + new, " AND scope = 'vm'")
        if "ha_protected_vms" in tables:
            row = db.execute("SELECT domain_xml FROM ha_protected_vms WHERE vm_name = ?", (new,)).fetchone()
            if row and row["domain_xml"]:
                db.execute(
                    "UPDATE ha_protected_vms SET domain_xml = ? WHERE vm_name = ?",
                    (_renamed_xml(row["domain_xml"], new), new),
                )
        if "backup_group_jobs" in tables:
            for row in db.execute("SELECT id, exclues FROM backup_group_jobs").fetchall():
                excluded = json.loads(row["exclues"] or "[]")
                if old in excluded:
                    excluded = sorted({new if v == old else v for v in excluded})
                    db.execute(
                        "UPDATE backup_group_jobs SET exclues = ? WHERE id = ?", (json.dumps(excluded), row["id"])
                    )
        db.commit()


def container_records(old, new):
    with get_conn() as db:
        tables = _tables(db)
        for table, column in _CONTAINER_COLUMNS:
            _move(db, tables, table, column, old, new)
        _move(db, tables, "acl", "resource_id", old, new, " AND resource_type = 'container'")
        _move(db, tables, "object_meta", "name", old, new, " AND kind = 'container'")
        db.commit()


def node_records(old, new):
    with get_conn() as db:
        tables = _tables(db)
        db.execute("UPDATE nodes SET name = ? WHERE name = ?", (new, old))
        for table, column in _NODE_COLUMNS:
            _move(db, tables, table, column, old, new)
        # The node's own notes and tags, then the VMs and containers it holds.
        _move(db, tables, "object_meta", "name", old, new, " AND kind = 'node'")
        _move(db, tables, "object_meta", "node", old, new, " AND kind != 'node'")
        if "shared_pools" in tables:  # the nodes a shared storage pool was created on
            for row in db.execute("SELECT nom, noeuds FROM shared_pools").fetchall():
                nodes = json.loads(row["noeuds"] or "[]")
                if old in nodes:
                    renamed = sorted({new if n == old else n for n in nodes})
                    db.execute("UPDATE shared_pools SET noeuds = ? WHERE nom = ?", (json.dumps(renamed), row["nom"]))
        if "metrics_samples" in tables:
            db.execute(
                "UPDATE metrics_samples SET cible = ? WHERE scope = 'host' AND cible = ?",
                (f"node:{new}", f"node:{old}"),
            )
            db.execute(
                "UPDATE metrics_samples SET cible = ? || substr(cible, ?) WHERE scope = 'vm' AND substr(cible, 1, ?) = ?",
                (f"{new}:", len(old) + 2, len(old) + 1, f"{old}:"),
            )
        db.commit()


def vm_in_k8s_cluster(name):
    """The Kubernetes cluster a VM belongs to, or None: those VMs are found by their name (app/core/k8s_cluster.py)."""
    with get_conn() as db:
        if "k8s_clusters" not in _tables(db):
            return None
        for row in db.execute("SELECT nom, serveur, workers FROM k8s_clusters").fetchall():
            if name == row["serveur"] or name in json.loads(row["workers"] or "[]"):
                return row["nom"]
    return None
