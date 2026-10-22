"""Shared storage pools declared for several nodes at once, as on Proxmox (Datacenter > Storage > Nodes).

An NFS export or an iSCSI target reachable from every node is the same storage for all of them: it is created on
each chosen node under the same name and mount point, which live migration and HA need. The definition is kept here
so that a pool declared for every node ("all") is also created on the nodes registered later. A CHAP or SMB password
is kept encrypted (app/core/secrets_crypto.py), never returned by the API.
"""

import json
from datetime import UTC, datetime

from app.core import secrets_crypto


def _store():
    from app.repositories import registry

    return registry.settings().sync


SHARED_TYPES = ("netfs", "smb", "iscsi")
# The fields of a pool definition kept for later nodes (PoolCreate, app/routers/storage.py).
FIELDS = (
    "name",
    "type",
    "nfs_host",
    "nfs_export_path",
    "nfs_version",
    "iscsi_host",
    "iscsi_port",
    "iscsi_target",
    "chap_user",
    "smb_host",
    "smb_share",
    "smb_user",
    "smb_domain",
    "smb_version",
)
# Kept encrypted, and only handed back to create the pool on a new node.
SECRETS = ("chap_password", "smb_password")


def save(definition, all_nodes, nodes, username):
    """definition: the PoolCreate fields (their passwords included, encrypted here)."""
    stored = {k: definition.get(k) for k in FIELDS}
    for key in SECRETS:
        if definition.get(key):
            stored[key] = secrets_crypto.encrypt(definition[key])
    _store().save_shared_pool(
        stored["name"],
        json.dumps(stored),
        1 if all_nodes else 0,
        json.dumps(sorted(set(nodes))),
        username,
        datetime.now(UTC).isoformat(),
    )


def _row(row, with_secret=False):
    definition = json.loads(row["definition"])
    for key in SECRETS:
        secret = definition.pop(key, None)
        if with_secret and secret:
            definition[key] = secrets_crypto.decrypt(secret)
    return {
        "name": row["name"],
        "type": definition.get("type"),
        "all_nodes": bool(row["all_nodes"]),
        "nodes": json.loads(row["nodes"] or "[]"),
        "definition": definition,
    }


def list_all():
    """Every shared definition, without any secret."""
    return [_row(r) for r in _store().shared_pools()]


def get(name, with_secret=False):
    row = _store().shared_pool(name)
    return _row(row, with_secret) if row else None


def for_every_node(with_secret=True):
    """The definitions to create on a node registered now."""
    return [_row(r, with_secret) for r in _store().shared_pools(every_node_only=True)]


def add_node(name, node):
    """Record that the pool now also exists on `node`."""
    entry = get(name)
    if entry and node not in entry["nodes"]:
        _store().set_shared_pool_nodes(name, json.dumps(sorted([*entry["nodes"], node])))


def rename(old, new):
    entry = get(old, with_secret=False)
    if not entry:
        return
    definition = json.loads(_store().shared_pool(old)["definition"])
    definition["name"] = new
    _store().rename_shared_pool(old, new, json.dumps(definition))


def delete(name):
    _store().delete_shared_pool(name)
