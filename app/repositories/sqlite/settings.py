"""Cluster-wide settings and definitions in SQLite: network firewalls, shared storage pools, Kubernetes clusters,
notification channels, the update check, application settings and the configuration copies sent to the nodes.

Secrets arrive and leave sealed (CHAP passwords, SMTP passwords, kubeconfigs and k3s tokens): the feature modules in
app/core encrypt and decrypt them.
"""

import asyncio
import json

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


# Columns update_k8s_cluster() and update_channel() may set: names are interpolated, values are bound.
K8S_COLUMNS = frozenset({"status", "error", "task_id", "kubeconfig", "token", "address", "version"})
CHANNEL_COLUMNS = frozenset({"name", "events", "enabled", "config"})


def _nodes_to_db(nodes):
    # A shared pool's nodes, a JSON list: "local" is stored as this node's name (app/core/self_node.py).
    return json.dumps([self_node.to_db(n) for n in json.loads(nodes or "[]")])


def _shared_pool(row):
    return {**row, "nodes": json.dumps([self_node.from_db(n) for n in json.loads(row["nodes"] or "[]")])}


class SqliteSettingsStore:
    # ---- Network firewalls ----

    def network_firewall(self, network_name):
        return _one(
            "SELECT network_name, default_policy, rules_json FROM network_firewall WHERE network_name = ?",
            (network_name,),
        )

    def network_firewalls(self):
        return _rows("SELECT network_name, default_policy, rules_json FROM network_firewall")

    def save_network_firewall(self, network_name, default_policy, rules_json):
        _write(
            "INSERT INTO network_firewall (network_name, default_policy, rules_json) VALUES (?, ?, ?) "
            "ON CONFLICT(network_name) DO UPDATE SET default_policy = excluded.default_policy, "
            "rules_json = excluded.rules_json",
            (network_name, default_policy, rules_json),
        )

    def delete_network_firewall(self, network_name):
        _write("DELETE FROM network_firewall WHERE network_name = ?", (network_name,))

    # ---- Shared storage pools ----

    def save_shared_pool(self, name, definition, all_nodes, nodes, username, now):
        _write(
            "INSERT INTO shared_pools (name, definition, all_nodes, nodes, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET definition = excluded.definition, "
            "all_nodes = excluded.all_nodes, nodes = excluded.nodes",
            (name, definition, all_nodes, _nodes_to_db(nodes), username, now),
        )

    def shared_pools(self, every_node_only=False):
        if every_node_only:
            rows = _rows("SELECT * FROM shared_pools WHERE all_nodes = 1 ORDER BY name")
        else:
            rows = _rows("SELECT * FROM shared_pools ORDER BY name")
        return [_shared_pool(r) for r in rows]

    def shared_pool(self, name):
        row = _one("SELECT * FROM shared_pools WHERE name = ?", (name,))
        return _shared_pool(row) if row else None

    def set_shared_pool_nodes(self, name, nodes):
        _write("UPDATE shared_pools SET nodes = ? WHERE name = ?", (_nodes_to_db(nodes), name))

    def rename_shared_pool(self, old, new, definition):
        _write("UPDATE OR REPLACE shared_pools SET name = ?, definition = ? WHERE name = ?", (new, definition, old))

    def delete_shared_pool(self, name):
        _write("DELETE FROM shared_pools WHERE name = ?", (name,))

    # ---- Kubernetes clusters ----

    def k8s_clusters(self):
        return _rows("SELECT * FROM k8s_clusters ORDER BY name")

    def k8s_cluster(self, name):
        return _one("SELECT * FROM k8s_clusters WHERE name = ?", (name,))

    def create_k8s_cluster(self, name, network, server, workers, vcpu, memory_mb, disk_gb, task_id, username, now):
        _write(
            "INSERT INTO k8s_clusters (name, network, server, workers, vcpu, memory_mb, disk_gb, status, task_id, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)",
            (name, network, server, workers, vcpu, memory_mb, disk_gb, task_id, username, now),
        )

    def update_k8s_cluster(self, name, fields):
        unknown = set(fields) - K8S_COLUMNS
        if unknown:
            raise ValueError(f"Unknown cluster field(s): {', '.join(sorted(unknown))}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        _write(f"UPDATE k8s_clusters SET {cols} WHERE name = ?", (*fields.values(), name))  # noqa: S608

    def fail_interrupted_k8s_clusters(self, message):
        _write(
            "UPDATE k8s_clusters SET status = 'failed', error = ? WHERE status IN ('creating', 'deleting')",
            (message,),
        )

    def delete_k8s_cluster(self, name):
        _write("DELETE FROM k8s_clusters WHERE name = ?", (name,))

    # ---- Notification channels ----

    def channels(self):
        return _rows("SELECT * FROM notification_channels ORDER BY id")

    def channel(self, channel_id):
        return _one("SELECT * FROM notification_channels WHERE id = ?", (channel_id,))

    def create_channel(self, type_, name, config, events, username, now):
        return _write(
            "INSERT INTO notification_channels (type, name, config, events, enabled, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (type_, name, config, events, username, now),
        ).lastrowid

    def update_channel(self, channel_id, fields):
        unknown = set(fields) - CHANNEL_COLUMNS
        if unknown:
            raise ValueError(f"Unknown channel field(s): {', '.join(sorted(unknown))}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            _write(f"UPDATE notification_channels SET {sets} WHERE id = ?", (*fields.values(), channel_id))  # noqa: S608

    def delete_channel(self, channel_id):
        return _write("DELETE FROM notification_channels WHERE id = ?", (channel_id,)).rowcount > 0

    # ---- Update check ----

    def last_notified_version(self):
        row = _one("SELECT last_notified_version FROM update_check_state WHERE id = 1")
        return row["last_notified_version"] if row else None

    def mark_update_notified(self, version, now):
        _write(
            "INSERT INTO update_check_state (id, last_notified_version, last_checked_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_notified_version = excluded.last_notified_version, "
            "last_checked_at = excluded.last_checked_at",
            (version, now),
        )

    def touch_update_checked(self, now):
        _write(
            "INSERT INTO update_check_state (id, last_checked_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_checked_at = excluded.last_checked_at",
            (now,),
        )

    # ---- Application settings ----

    def app_setting(self, key):
        row = _one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_app_setting(self, key, value):
        _write(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---- Configuration copies sent to the nodes ----

    def record_config_copy(self, node, at, status, size, fingerprint, error):
        _write(
            "INSERT INTO config_copies (node, copied_at, status, size, fingerprint, error) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(node) DO UPDATE SET copied_at = excluded.copied_at, status = excluded.status, "
            "size = COALESCE(excluded.size, config_copies.size), "
            "fingerprint = COALESCE(excluded.fingerprint, config_copies.fingerprint), error = excluded.error",
            (node, at, status, size, fingerprint, error),
        )

    def config_copies(self):
        return _rows("SELECT * FROM config_copies ORDER BY node")


class SqliteSettingsRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteSettingsStore()

    async def shared_pools(self):
        return await asyncio.to_thread(self.sync.shared_pools)

    async def k8s_clusters(self):
        return await asyncio.to_thread(self.sync.k8s_clusters)

    async def channels(self):
        return await asyncio.to_thread(self.sync.channels)

    async def app_setting(self, key):
        return await asyncio.to_thread(self.sync.app_setting, key)
