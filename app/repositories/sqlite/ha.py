"""High availability in SQLite: protected VMs (`ha_protected_vms`), the watcher's settings (`ha_settings`) and each
node's fencing (`node_fencing`).

The fencing password arrives sealed (app/core/secrets_crypto.py) and leaves sealed: only the fence agent call
decrypts it.
"""

import asyncio

from app.core import self_node
from app.core.database import get_conn


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _rows(sql, params=()):
    with get_conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


def _protected(row):
    return {**row, "node": self_node.from_db(row["node"])}


class SqliteHaStore:
    # ---- Protected VMs ----

    def protected(self):
        return [_protected(r) for r in _rows("SELECT * FROM ha_protected_vms ORDER BY vm_name")]

    def protected_vm(self, vm_name):
        row = _one("SELECT * FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))
        return _protected(row) if row else None

    def protect(self, vm_name, node, domain_xml, username, now):
        _write(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET node=excluded.node, domain_xml=excluded.domain_xml, "
            "last_synced_at=excluded.last_synced_at",
            (vm_name, self_node.to_db(node), domain_xml, username, now, now),
        )

    def unprotect(self, vm_name):
        _write("DELETE FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))

    def sync_definition(self, vm_name, domain_xml, now, node=None):
        """A fresher copy of the VM's definition; `node` too after a recovery onto another node."""
        if node is None:
            # The definition is written only when it changed: it is a replicated column, and each write goes out to
            # every node of a cluster. The time of the check stays on this node (a volatile column).
            _write("UPDATE ha_protected_vms SET last_synced_at = ? WHERE vm_name = ?", (now, vm_name))
            _write(
                "UPDATE ha_protected_vms SET domain_xml = ? WHERE vm_name = ? AND domain_xml IS NOT ?",
                (domain_xml, vm_name, domain_xml),
            )
        else:
            _write(
                "UPDATE ha_protected_vms SET node = ?, domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                (self_node.to_db(node), domain_xml, now, vm_name),
            )

    def move_protected(self, vm_name, node):
        return (
            _write("UPDATE ha_protected_vms SET node = ? WHERE vm_name = ?", (self_node.to_db(node), vm_name)).rowcount
            > 0
        )

    def record_watch(self, vm_name, state, action, now):
        _write(
            "UPDATE ha_protected_vms SET ha_state = ?, last_action = ?, last_action_at = ? WHERE vm_name = ?",
            (state, action, now, vm_name),
        )

    # ---- Watcher settings ----

    def settings(self):
        return {r["key"]: r["value"] for r in _rows("SELECT key, value FROM ha_settings")}

    def save_settings(self, values):
        with get_conn() as db:
            for key, value in values.items():
                db.execute(
                    "INSERT INTO ha_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            db.commit()

    # ---- Fencing ----

    def fencing(self, node):
        """The node's row, sealed secret included, or None."""
        row = _one("SELECT * FROM node_fencing WHERE node = ?", (self_node.to_db(node),))
        return {**row, "node": self_node.from_db(row["node"])} if row else None

    def fenced_nodes(self):
        return [self_node.from_db(r["node"]) for r in _rows("SELECT node FROM node_fencing ORDER BY node")]

    def save_fencing(self, node, method, address, port, user, secret, tls_unverified, username, now):
        _write(
            "INSERT INTO node_fencing (node, method, address, port, user, secret, tls_unverified, "
            "modified_by, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET "
            "method=excluded.method, address=excluded.address, port=excluded.port, user=excluded.user, "
            "secret=excluded.secret, tls_unverified=excluded.tls_unverified, modified_by=excluded.modified_by, "
            "modified_at=excluded.modified_at",
            (self_node.to_db(node), method, address, port, user, secret, tls_unverified, username, now),
        )

    def delete_fencing(self, node):
        return _write("DELETE FROM node_fencing WHERE node = ?", (self_node.to_db(node),)).rowcount > 0


class SqliteHaRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteHaStore()

    async def protected(self):
        return await asyncio.to_thread(self.sync.protected)

    async def protected_vm(self, vm_name):
        return await asyncio.to_thread(self.sync.protected_vm, vm_name)

    async def settings(self):
        return await asyncio.to_thread(self.sync.settings)
