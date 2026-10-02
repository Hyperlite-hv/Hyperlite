"""Renames in SQLite: every row that names a VM, a container, a node, a storage pool or a network moves to the new
name in one transaction; objects known by an id only get a new label.

The rules about what follows an object live in app/core/renaming.py; this module knows the tables.
"""

import asyncio
import json

from app.core import self_node
from app.core.database import get_conn

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


# Objects known by an id, whose name is only a label: renaming them changes nothing else.
LABELS = {
    "pool": ("pools", "name"),
    "group": ("groups", "name"),
    "custom_role": ("custom_roles", "name"),
    "job": ("jobs", "name"),
    "api_token": ("api_tokens", "name"),
}

# What rename_label() reports when the new name belongs to another object.
TAKEN = object()


class SqliteRenameStore:
    def vm_records(self, old, new, node_key, metrics_prefix, renamed_xml):
        """Move a VM's records from `old` to `new`; `renamed_xml(xml, new)` rewrites HA's copy of its definition."""
        with get_conn() as db:
            tables = _tables(db)
            for table, column in _VM_COLUMNS:
                _move(db, tables, table, column, old, new)
            _move(db, tables, "acl", "resource_id", old, new, " AND resource_type = 'vm'")
            shared_key = self_node.to_db(node_key)  # shared tables name the node (app/core/self_node.py)
            _move(db, tables, "vm_boot", "vm_name", old, new, " AND node = ?", (shared_key,))
            _move(db, tables, "object_meta", "name", old, new, " AND kind = 'vm' AND node = ?", (shared_key,))
            _move(db, tables, "job_steps", "cible", old, new, " AND cible_type = 'vm'")
            _move(
                db, tables, "metrics_samples", "cible", metrics_prefix + old, metrics_prefix + new, " AND scope = 'vm'"
            )
            if "ha_protected_vms" in tables:
                row = db.execute("SELECT domain_xml FROM ha_protected_vms WHERE vm_name = ?", (new,)).fetchone()
                if row and row["domain_xml"]:
                    db.execute(
                        "UPDATE ha_protected_vms SET domain_xml = ? WHERE vm_name = ?",
                        (renamed_xml(row["domain_xml"], new), new),
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

    def container_records(self, old, new):
        with get_conn() as db:
            tables = _tables(db)
            for table, column in _CONTAINER_COLUMNS:
                _move(db, tables, table, column, old, new)
            _move(db, tables, "acl", "resource_id", old, new, " AND resource_type = 'container'")
            _move(db, tables, "object_meta", "name", old, new, " AND kind = 'container'")
            db.commit()

    def node_records(self, old, new):
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
                        db.execute(
                            "UPDATE shared_pools SET noeuds = ? WHERE nom = ?", (json.dumps(renamed), row["nom"])
                        )
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

    def storage_pool_records(self, old, new, node_key):
        """A storage pool renamed on one node: the containers stored in it (this host) and its usage history."""
        with get_conn() as db:
            tables = _tables(db)
            if node_key == "local":
                _move(db, tables, "container_storage", "pool", old, new)
            _move(db, tables, "storage_samples", "pool", old, new, " AND node = ?", (node_key,))
            db.commit()

    def network_records(self, old, new):
        """A network renamed on this host: its firewall rules, the application containers and Kubernetes clusters on it."""
        with get_conn() as db:
            tables = _tables(db)
            _move(db, tables, "network_firewall", "network_name", old, new)
            _move(db, tables, "container_apps", "network", old, new)
            _move(db, tables, "k8s_clusters", "reseau", old, new)
            db.commit()

    def vm_in_k8s_cluster(self, name):
        """The Kubernetes cluster a VM belongs to, or None: those VMs are found by their name (app/core/k8s_cluster.py)."""
        with get_conn() as db:
            if "k8s_clusters" not in _tables(db):
                return None
            for row in db.execute("SELECT nom, serveur, workers FROM k8s_clusters").fetchall():
                if name == row["serveur"] or name in json.loads(row["workers"] or "[]"):
                    return row["nom"]
        return None

    def rename_label(self, kind, object_id, new, owner=None):
        """Rename an object of LABELS; its old name, or None when there is no such object (for `owner`, when given).
        TAKEN when another one already has the name (the tables require unique names)."""
        table, column = LABELS[kind]
        where, params = "id = ?", [object_id]
        if owner is not None:
            where, params = "id = ? AND username = ?", [object_id, owner]
        # Fixed table and column names from LABELS; every value is a parameter.
        select = f"SELECT {column} FROM {table} WHERE {where}"  # noqa: S608
        taken = f"SELECT 1 FROM {table} WHERE {column} = ? AND id != ?"  # noqa: S608
        update = f"UPDATE {table} SET {column} = ? WHERE id = ?"  # noqa: S608
        with get_conn() as db:
            row = db.execute(select, params).fetchone()
            if not row:
                return None
            # API tokens are named per account, and two may share a name.
            if kind != "api_token" and db.execute(taken, (new, object_id)).fetchone():
                return TAKEN
            db.execute(update, (new, object_id))
            db.commit()
        return row[column]


class SqliteRenameRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteRenameStore()

    async def vm_in_k8s_cluster(self, name):
        return await asyncio.to_thread(self.sync.vm_in_k8s_cluster, name)
