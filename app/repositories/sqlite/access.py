"""Access control in SQLite: `custom_roles`, `groups`, `group_members`, `pools`, `pool_members` and `acl`.

Rows only: resolving a role to its privileges and deciding a permission stay in app/core/permissions.py.
"""

import asyncio

from app.core.database import get_conn


def _rows(sql, params=()):
    with get_conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


def _write_all(*statements):
    """Several statements in one transaction."""
    with get_conn() as db:
        for sql, params in statements:
            db.execute(sql, params)
        db.commit()


class SqliteAccessStore:
    # ---- Custom roles ----

    def custom_roles(self):
        return _rows("SELECT id, name, privileges FROM custom_roles ORDER BY name")

    def custom_role(self, role_id):
        rows = _rows("SELECT id, name, privileges FROM custom_roles WHERE id = ?", (role_id,))
        return rows[0] if rows else None

    def create_custom_role(self, name, privileges):
        return _write("INSERT INTO custom_roles (name, privileges) VALUES (?, ?)", (name, privileges)).lastrowid

    def delete_custom_role(self, role_key, role_id):
        _write_all(
            ("DELETE FROM acl WHERE role = ?", (role_key,)),
            ("DELETE FROM custom_roles WHERE id = ?", (role_id,)),
        )

    # ---- Groups ----

    def groups(self):
        with get_conn() as db:
            groups = [dict(r) for r in db.execute("SELECT id, name FROM groups ORDER BY name").fetchall()]
            for g in groups:
                rows = db.execute(
                    "SELECT username FROM group_members WHERE group_id = ? ORDER BY username", (g["id"],)
                ).fetchall()
                g["membres"] = [r["username"] for r in rows]
        return groups

    def create_group(self, name):
        return _write("INSERT INTO groups (name) VALUES (?)", (name,)).lastrowid

    def delete_group(self, group_id):
        _write_all(
            ("DELETE FROM group_members WHERE group_id = ?", (group_id,)),
            ("DELETE FROM acl WHERE subject_type = 'group' AND subject_id = ?", (str(group_id),)),
            ("DELETE FROM groups WHERE id = ?", (group_id,)),
        )

    def add_group_member(self, group_id, username):
        _write("INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)", (group_id, username))

    def remove_group_member(self, group_id, username):
        _write("DELETE FROM group_members WHERE group_id = ? AND username = ?", (group_id, username))

    def groups_of(self, username):
        return [r["group_id"] for r in _rows("SELECT group_id FROM group_members WHERE username = ?", (username,))]

    # ---- Pools ----

    def pools(self):
        with get_conn() as db:
            pools = [dict(r) for r in db.execute("SELECT id, name, description FROM pools ORDER BY name").fetchall()]
            for p in pools:
                rows = db.execute(
                    "SELECT vm_name FROM pool_members WHERE pool_id = ? ORDER BY vm_name", (p["id"],)
                ).fetchall()
                p["vms"] = [r["vm_name"] for r in rows]
        return pools

    def create_pool(self, name, description):
        return _write("INSERT INTO pools (name, description) VALUES (?, ?)", (name, description)).lastrowid

    def delete_pool(self, pool_id):
        _write_all(
            ("DELETE FROM pool_members WHERE pool_id = ?", (pool_id,)),
            ("DELETE FROM acl WHERE resource_type = 'pool' AND resource_id = ?", (str(pool_id),)),
            ("DELETE FROM pools WHERE id = ?", (pool_id,)),
        )

    def add_pool_member(self, pool_id, vm_name):
        _write("INSERT OR IGNORE INTO pool_members (pool_id, vm_name) VALUES (?, ?)", (pool_id, vm_name))

    def remove_pool_member(self, pool_id, vm_name):
        _write("DELETE FROM pool_members WHERE pool_id = ? AND vm_name = ?", (pool_id, vm_name))

    def rename_pool_member(self, old_vm_name, new_vm_name):
        _write_all(
            ("UPDATE pool_members SET vm_name = ? WHERE vm_name = ?", (new_vm_name, old_vm_name)),
            ("DELETE FROM acl WHERE resource_type = 'vm' AND resource_id = ?", (old_vm_name,)),
        )

    def pools_of(self, vm_name):
        return [r["pool_id"] for r in _rows("SELECT pool_id FROM pool_members WHERE vm_name = ?", (vm_name,))]

    def remove_vm_from_all_pools(self, vm_name):
        _write("DELETE FROM pool_members WHERE vm_name = ?", (vm_name,))

    # ---- ACL ----

    def acl(self, resource_type=None):
        if resource_type is None:
            return _rows("SELECT id, subject_type, subject_id, role, resource_type, resource_id FROM acl ORDER BY id")
        return _rows(
            "SELECT id, subject_type, subject_id, role, resource_type, resource_id FROM acl WHERE resource_type = ? "
            "ORDER BY id",
            (resource_type,),
        )

    def create_acl(self, subject_type, subject_id, role, resource_type, resource_id):
        return _write(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) VALUES (?, ?, ?, ?, ?)",
            (subject_type, subject_id, role, resource_type, resource_id),
        ).lastrowid

    def delete_acl(self, acl_id):
        _write("DELETE FROM acl WHERE id = ?", (acl_id,))

    def delete_acl_for_vm(self, vm_name):
        _write("DELETE FROM acl WHERE resource_type = 'vm' AND resource_id = ?", (vm_name,))


class SqliteAccessRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteAccessStore()

    async def groups(self):
        return await asyncio.to_thread(self.sync.groups)

    async def pools(self):
        return await asyncio.to_thread(self.sync.pools)

    async def acl(self, resource_type=None):
        return await asyncio.to_thread(self.sync.acl, resource_type)

    async def custom_roles(self):
        return await asyncio.to_thread(self.sync.custom_roles)
