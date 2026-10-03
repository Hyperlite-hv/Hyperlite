"""This node's own name, for the tables every node of a cluster shares (app/repositories/cfs/tables.py).

The application and the API call the node that answers "local", and keep doing so. A replicated row cannot: "local"
written by one node would mean another machine on every other node. So the stores of the shared tables that name a
node translate at the database boundary: to_db() writes this node's name where the caller said "local", from_db()
gives "local" back for rows that name this node. Rows naming another node read as that node, as a registered one.

The name is HYPERLITE_NODE_NAME when set, else the one recorded at the first start (app_settings, a row each node
keeps for itself), else derived from the host name and recorded then: renaming the host later changes nothing.
Joining a cluster sets it to the name the cluster knows the node by.
"""

import json
import os
import re
import socket
import threading

LOCAL = "local"
SETTING = "node_name"
TASKS_SETTING = "tasks_node_names"  # the one-time rewrite of the tasks recorded under a host name (migrate)
# The names nodes are registered under (app/routers/nodes.py), lowercase.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")

_lock = threading.Lock()
_name = None


def _settings():
    from app.repositories.sqlite.settings import SqliteSettingsStore

    return SqliteSettingsStore()


def _from_hostname():
    raw = socket.gethostname().split(".")[0].lower()
    name = re.sub(r"[^a-z0-9-]", "-", raw).strip("-")
    if not NAME_RE.match(name) or name == LOCAL:
        name = "node-" + (name or "1")
    return name[:63]


def name():
    """This node's name in the shared tables."""
    global _name
    forced = (os.environ.get("HYPERLITE_NODE_NAME") or "").strip().lower()
    if forced:
        if not NAME_RE.match(forced) or forced == LOCAL:
            raise ValueError(f"HYPERLITE_NODE_NAME '{forced}' is not a valid node name")
        return forced
    with _lock:
        if _name is None:
            stored = _settings().app_setting(SETTING)
            if not stored:
                stored = _from_hostname()
                _settings().set_app_setting(SETTING, stored)
            _name = stored
        return _name


def forget():
    """Read the name again at the next call (after it was set, and between tests that change the database)."""
    global _name
    with _lock:
        _name = None


def is_self(node):
    return node in (None, "", LOCAL) or node == name()


def to_db(node):
    """The value a shared table stores for `node`: this node's name for None, "" and "local"."""
    return name() if node in (None, "", LOCAL) else node


def from_db(node):
    """The value callers expect: "local" for this node's own rows."""
    return LOCAL if node is not None and node == name() else node


# (table, column) of the shared tables that name a node, "local" before this module existed.
_NODE_COLUMNS = (
    ("vm_boot", "node", ""),
    ("object_meta", "node", " AND kind != 'node'"),
    ("object_meta", "name", " AND kind = 'node'"),
    ("node_maintenance", "node", ""),
    ("node_fencing", "node", ""),
    ("ha_protected_vms", "node", ""),
)


def migrate(db):
    """Run by init_db in its transaction: record this node's name and rewrite the "local" rows of the shared tables
    under it. A leftover "local" row replaces a row that already names the node (it is the newer one)."""
    global _name
    forced = (os.environ.get("HYPERLITE_NODE_NAME") or "").strip().lower()
    row = db.execute("SELECT valeur FROM app_settings WHERE cle = ?", (SETTING,)).fetchone()
    own = forced or (row[0] if row else None) or _from_hostname()
    if not row:
        db.execute("INSERT INTO app_settings (cle, valeur) VALUES (?, ?)", (SETTING, own))
    for table, column, where in _NODE_COLUMNS:
        # Names come from the list above, values are bound.
        db.execute(
            f"UPDATE OR REPLACE {table} SET {column} = ? WHERE {column} = 'local'{where}",  # noqa: S608
            (own,),
        )
    for nom, noeuds in db.execute("SELECT nom, noeuds FROM shared_pools").fetchall():
        nodes = json.loads(noeuds or "[]")
        if LOCAL in nodes:
            fixed = sorted({own if n == LOCAL else n for n in nodes})
            db.execute("UPDATE shared_pools SET noeuds = ? WHERE nom = ?", (json.dumps(fixed), nom))
    # The tasks are this node's own history. Before the node had a name of its own they recorded the host name
    # libvirt gave (it changes with the host: "hyperlite.home", then "antho"), "local" or nothing, so the node's
    # pages showed none of them. Once: every task that names no registered node is this node's.
    if not db.execute("SELECT 1 FROM app_settings WHERE cle = ?", (TASKS_SETTING,)).fetchone():
        known = [r[0] for r in db.execute("SELECT name FROM nodes")] + [own]
        marks = ",".join("?" * len(known))
        db.execute(f"UPDATE tasks SET node = ? WHERE node IS NULL OR node NOT IN ({marks})", (own, *known))  # noqa: S608
        db.execute("INSERT INTO app_settings (cle, valeur) VALUES (?, '1')", (TASKS_SETTING,))
    with _lock:
        _name = None if forced else own
