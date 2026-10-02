"""Nodes in SQLite: the `nodes`, `node_maintenance` and `node_live` tables (app/core/database.py).

The queries run in a worker thread behind the asynchronous NodeRepository methods. The synchronous code that still
needs them (the node poller and the metrics collector threads, libvirt connection helpers) uses `SqliteNodeStore`
directly through `.sync`: a transitional bridge, removed as those callers become asynchronous.
"""

import asyncio
import sqlite3

from app.core import self_node
from app.core.database import get_conn
from app.domain.common import AlreadyExists
from app.domain.node import Maintenance, Node, NodeSpec, NodeStatus

ONLINE = "en_ligne"


def _node(row):
    return Node(
        id=row["id"],
        spec=NodeSpec(name=row["name"], hostname=row["hostname"], ssh_user=row["ssh_user"], ssh_port=row["ssh_port"]),
        status=NodeStatus(statut=row["statut"], derniere_verification=row["derniere_verification"]),
        added_at=row["added_at"],
    )


def _maintenance(row):
    return Maintenance(**{**dict(row), "node": self_node.from_db(row["node"])})


def _live(row):
    d = dict(row)
    d["joignable"] = bool(d["joignable"])
    d["mesure_le"] = d.pop("ts")
    d.pop("name", None)
    return d


class SqliteNodeStore:
    """The synchronous queries."""

    # In a cluster the nodes table lists every member, this node included (it is shared); this node is "local" to
    # itself, never a remote node to reach over SSH, so its own row is left out here.

    def get(self, name):
        if self_node.is_self(name):
            return None
        with get_conn() as db:
            row = db.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
        return _node(row) if row else None

    def list(self):
        with get_conn() as db:
            rows = db.execute("SELECT * FROM nodes ORDER BY name").fetchall()
        return [_node(r) for r in rows if not self_node.is_self(r["name"])]

    def list_online(self):
        with get_conn() as db:
            rows = db.execute("SELECT * FROM nodes WHERE statut = ? ORDER BY name", (ONLINE,)).fetchall()
        return [_node(r) for r in rows if not self_node.is_self(r["name"])]

    def create(self, spec, added_at, statut):
        with get_conn() as db:
            try:
                db.execute(
                    "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, derniere_verification, added_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (spec.name, spec.hostname, spec.ssh_user, spec.ssh_port, statut, added_at, added_at),
                )
                db.commit()
            except sqlite3.IntegrityError as e:
                raise AlreadyExists(f"A node '{spec.name}' already exists") from e
        return self.get(spec.name)

    def delete(self, name):
        with get_conn() as db:
            cur = db.execute("DELETE FROM nodes WHERE name = ?", (name,))
            db.commit()
        return cur.rowcount > 0

    def update_status(self, node_id, statut, checked_at):
        with get_conn() as db:
            prev = db.execute("SELECT statut FROM nodes WHERE id = ?", (node_id,)).fetchone()
            db.execute(
                "UPDATE nodes SET statut = ?, derniere_verification = ? WHERE id = ?", (statut, checked_at, node_id)
            )
            db.commit()
        return prev["statut"] if prev else None

    def live(self, name=None):
        with get_conn() as db:
            if name is not None:
                row = db.execute("SELECT * FROM node_live WHERE name = ?", (name,)).fetchone()
                return _live(row) if row else None
            return {r["name"]: _live(r) for r in db.execute("SELECT * FROM node_live").fetchall()}

    def write_live(self, rows):
        with get_conn() as db:
            db.executemany(
                "INSERT OR REPLACE INTO node_live (name, ts, joignable, cpu_pct, mem_used_mb, mem_total_mb, uptime_s, "
                "cores, cpu_model, kernel, os, address, version_hyperviseur, version_libvirt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            db.commit()

    def get_maintenance(self, node):
        with get_conn() as db:
            row = db.execute("SELECT * FROM node_maintenance WHERE node = ?", (self_node.to_db(node),)).fetchone()
        return _maintenance(row) if row else None

    def list_maintenance(self):
        with get_conn() as db:
            return [_maintenance(r) for r in db.execute("SELECT * FROM node_maintenance ORDER BY node")]

    def set_maintenance(self, node, username, started_at):
        with get_conn() as db:
            db.execute(
                "INSERT INTO node_maintenance (node, started_by, started_at) VALUES (?, ?, ?) ON CONFLICT(node) DO NOTHING",
                (self_node.to_db(node), username, started_at),
            )
            db.commit()

    def clear_maintenance(self, node):
        with get_conn() as db:
            cur = db.execute("DELETE FROM node_maintenance WHERE node = ?", (self_node.to_db(node),))
            db.commit()
        return cur.rowcount > 0


class SqliteNodeRepository:
    """NodeRepository over SQLite."""

    def __init__(self, store=None):
        self.sync = store or SqliteNodeStore()

    async def get(self, name):
        return await asyncio.to_thread(self.sync.get, name)

    async def list(self):
        return await asyncio.to_thread(self.sync.list)

    async def list_online(self):
        return await asyncio.to_thread(self.sync.list_online)

    async def create(self, spec, added_at, statut):
        return await asyncio.to_thread(self.sync.create, spec, added_at, statut)

    async def delete(self, name):
        return await asyncio.to_thread(self.sync.delete, name)

    async def update_status(self, node_id, statut, checked_at):
        return await asyncio.to_thread(self.sync.update_status, node_id, statut, checked_at)

    async def live(self, name=None):
        return await asyncio.to_thread(self.sync.live, name)

    async def write_live(self, rows):
        await asyncio.to_thread(self.sync.write_live, rows)

    async def get_maintenance(self, node):
        return await asyncio.to_thread(self.sync.get_maintenance, node)

    async def list_maintenance(self):
        return await asyncio.to_thread(self.sync.list_maintenance)

    async def set_maintenance(self, node, username, started_at):
        await asyncio.to_thread(self.sync.set_maintenance, node, username, started_at)

    async def clear_maintenance(self, node):
        return await asyncio.to_thread(self.sync.clear_maintenance, node)

    async def acquire_lease(self, name, ttl_seconds):
        raise NotImplementedError("Node leases come with etcd (phase 6)")
