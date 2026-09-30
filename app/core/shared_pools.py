"""Shared storage pools declared for several nodes at once, as on Proxmox (Datacenter > Storage > Nodes).

An NFS export or an iSCSI target reachable from every node is the same storage for all of them: it is created on
each chosen node under the same name and mount point, which live migration and HA need. The definition is kept here
so that a pool declared for every node ("tous") is also created on the nodes registered later. A CHAP password is
kept encrypted (app/core/secrets_crypto.py), never returned by the API.
"""

import json
from datetime import UTC, datetime

from app.core import secrets_crypto
from app.core.database import get_conn

SHARED_TYPES = ("netfs", "iscsi")
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
)


def save(definition, all_nodes, nodes, username):
    """definition: the PoolCreate fields (chap_password included, encrypted here)."""
    stored = {k: definition.get(k) for k in FIELDS}
    if definition.get("chap_password"):
        stored["chap_password"] = secrets_crypto.encrypt(definition["chap_password"])
    with get_conn() as db:
        db.execute(
            "INSERT INTO shared_pools (nom, definition, tous_les_noeuds, noeuds, cree_par, cree_le) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(nom) DO UPDATE SET definition = excluded.definition, tous_les_noeuds = excluded.tous_les_noeuds, "
            "noeuds = excluded.noeuds",
            (
                stored["name"],
                json.dumps(stored),
                1 if all_nodes else 0,
                json.dumps(sorted(set(nodes))),
                username,
                datetime.now(UTC).isoformat(),
            ),
        )
        db.commit()


def _row(row, with_secret=False):
    definition = json.loads(row["definition"])
    secret = definition.pop("chap_password", None)
    if with_secret and secret:
        definition["chap_password"] = secrets_crypto.decrypt(secret)
    return {
        "nom": row["nom"],
        "type": definition.get("type"),
        "tous_les_noeuds": bool(row["tous_les_noeuds"]),
        "noeuds": json.loads(row["noeuds"] or "[]"),
        "definition": definition,
    }


def list_all():
    """Every shared definition, without any secret."""
    with get_conn() as db:
        return [_row(r) for r in db.execute("SELECT * FROM shared_pools ORDER BY nom").fetchall()]


def get(name, with_secret=False):
    with get_conn() as db:
        row = db.execute("SELECT * FROM shared_pools WHERE nom = ?", (name,)).fetchone()
    return _row(row, with_secret) if row else None


def for_every_node(with_secret=True):
    """The definitions to create on a node registered now."""
    with get_conn() as db:
        rows = db.execute("SELECT * FROM shared_pools WHERE tous_les_noeuds = 1 ORDER BY nom").fetchall()
    return [_row(r, with_secret) for r in rows]


def add_node(name, node):
    """Record that the pool now also exists on `node`."""
    entry = get(name)
    if entry and node not in entry["noeuds"]:
        with get_conn() as db:
            db.execute(
                "UPDATE shared_pools SET noeuds = ? WHERE nom = ?", (json.dumps(sorted([*entry["noeuds"], node])), name)
            )
            db.commit()


def delete(name):
    with get_conn() as db:
        db.execute("DELETE FROM shared_pools WHERE nom = ?", (name,))
        db.commit()
