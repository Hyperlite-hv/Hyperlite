"""High availability in SQLite: protected VMs (`ha_protected_vms`), the watcher's settings (`ha_settings`) and each
node's fencing (`node_fencing`).

The fencing password arrives sealed (app/core/secrets_crypto.py) and leaves sealed: only the fence agent call
decrypts it.
"""

import asyncio

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


class SqliteHaStore:
    # ---- Protected VMs ----

    def protected(self):
        return _rows("SELECT * FROM ha_protected_vms ORDER BY vm_name")

    def protected_vm(self, vm_name):
        return _one("SELECT * FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))

    def protect(self, vm_name, node, domain_xml, username, now):
        _write(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET node=excluded.node, domain_xml=excluded.domain_xml, "
            "last_synced_at=excluded.last_synced_at",
            (vm_name, node, domain_xml, username, now, now),
        )

    def unprotect(self, vm_name):
        _write("DELETE FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))

    def sync_definition(self, vm_name, domain_xml, now, node=None):
        """A fresher copy of the VM's definition; `node` too after a recovery onto another node."""
        if node is None:
            _write(
                "UPDATE ha_protected_vms SET domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                (domain_xml, now, vm_name),
            )
        else:
            _write(
                "UPDATE ha_protected_vms SET node = ?, domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                (node, domain_xml, now, vm_name),
            )

    def move_protected(self, vm_name, node):
        return _write("UPDATE ha_protected_vms SET node = ? WHERE vm_name = ?", (node, vm_name)).rowcount > 0

    def record_watch(self, vm_name, etat, action, now):
        _write(
            "UPDATE ha_protected_vms SET etat_ha = ?, derniere_action = ?, derniere_action_le = ? WHERE vm_name = ?",
            (etat, action, now, vm_name),
        )

    # ---- Watcher settings ----

    def settings(self):
        return {r["cle"]: r["valeur"] for r in _rows("SELECT cle, valeur FROM ha_settings")}

    def save_settings(self, values):
        with get_conn() as db:
            for key, value in values.items():
                db.execute(
                    "INSERT INTO ha_settings (cle, valeur) VALUES (?, ?) "
                    "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
                    (key, value),
                )
            db.commit()

    # ---- Fencing ----

    def fencing(self, node):
        """The node's row, sealed secret included, or None."""
        return _one("SELECT * FROM node_fencing WHERE node = ?", (node,))

    def fenced_nodes(self):
        return [r["node"] for r in _rows("SELECT node FROM node_fencing ORDER BY node")]

    def save_fencing(self, node, methode, adresse, port, utilisateur, secret, tls_non_verifie, username, now):
        _write(
            "INSERT INTO node_fencing (node, methode, adresse, port, utilisateur, secret, tls_non_verifie, "
            "modifie_par, modifie_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET "
            "methode=excluded.methode, adresse=excluded.adresse, port=excluded.port, utilisateur=excluded.utilisateur, "
            "secret=excluded.secret, tls_non_verifie=excluded.tls_non_verifie, modifie_par=excluded.modifie_par, "
            "modifie_le=excluded.modifie_le",
            (node, methode, adresse, port, utilisateur, secret, tls_non_verifie, username, now),
        )

    def delete_fencing(self, node):
        return _write("DELETE FROM node_fencing WHERE node = ?", (node,)).rowcount > 0


class SqliteHaRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteHaStore()

    async def protected(self):
        return await asyncio.to_thread(self.sync.protected)

    async def protected_vm(self, vm_name):
        return await asyncio.to_thread(self.sync.protected_vm, vm_name)

    async def settings(self):
        return await asyncio.to_thread(self.sync.settings)
