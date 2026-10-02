"""What Hyperlite keeps beside a VM or a container, in SQLite: the SSH user, the declared OS, an unattended install in
progress, the inactivity policy, the cloud-init state, start at boot, notes and tags, and for containers the
application image and the storage pool.

Rows only: validation, defaults and libvirt stay in the app/core modules that own each feature.
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


class SqliteObjectStore:
    # ---- VMs: SSH user, OS label, unattended installation ----

    def set_vm_ssh_user(self, vm_name, username):
        _write(
            "INSERT INTO vm_ssh_users (vm_name, username) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET username = excluded.username",
            (vm_name, username),
        )

    def vm_ssh_user(self, vm_name):
        row = _one("SELECT username FROM vm_ssh_users WHERE vm_name = ?", (vm_name,))
        return row["username"] if row else None

    def vm_ssh_users(self):
        """{vm_name: username} for every VM: one query for the VM list instead of one per VM."""
        return {r["vm_name"]: r["username"] for r in _rows("SELECT vm_name, username FROM vm_ssh_users")}

    def delete_vm_ssh_user(self, vm_name):
        _write("DELETE FROM vm_ssh_users WHERE vm_name = ?", (vm_name,))

    def set_vm_os_label(self, vm_name, os_label):
        _write(
            "INSERT INTO vm_os_label (vm_name, os_label) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_label = excluded.os_label",
            (vm_name, os_label),
        )

    def vm_os_label(self, vm_name):
        row = _one("SELECT os_label FROM vm_os_label WHERE vm_name = ?", (vm_name,))
        return row["os_label"] if row else None

    def vm_os_labels(self):
        """{vm_name: os_label} for every VM."""
        return {r["vm_name"]: r["os_label"] for r in _rows("SELECT vm_name, os_label FROM vm_os_label")}

    def delete_vm_os_label(self, vm_name):
        _write("DELETE FROM vm_os_label WHERE vm_name = ?", (vm_name,))

    def mark_provisioning(self, vm_name, os_family, started_at, task_id):
        _write(
            "INSERT INTO vm_provisioning (vm_name, os_family, started_at, task_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_family = excluded.os_family, started_at = excluded.started_at, "
            "task_id = excluded.task_id",
            (vm_name, os_family, started_at, task_id),
        )

    def provisioning(self, vm_name):
        return _one("SELECT os_family, started_at, task_id FROM vm_provisioning WHERE vm_name = ?", (vm_name,))

    def clear_provisioning(self, vm_name):
        _write("DELETE FROM vm_provisioning WHERE vm_name = ?", (vm_name,))

    # ---- VMs: automatic deletion when inactive ----

    def set_auto_cleanup(self, vm_name, inactive_days, now):
        _write(
            "INSERT INTO vm_auto_cleanup (vm_name, inactive_days, last_active_at, warned_at, created_at) "
            "VALUES (?, ?, ?, NULL, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET inactive_days = excluded.inactive_days, "
            "last_active_at = excluded.last_active_at, warned_at = NULL",
            (vm_name, inactive_days, now, now),
        )

    def auto_cleanup(self, vm_name):
        return _one(
            "SELECT inactive_days, last_active_at, warned_at, created_at FROM vm_auto_cleanup WHERE vm_name = ?",
            (vm_name,),
        )

    def delete_auto_cleanup(self, vm_name):
        _write("DELETE FROM vm_auto_cleanup WHERE vm_name = ?", (vm_name,))

    def touch_activity(self, vm_name, now):
        _write("UPDATE vm_auto_cleanup SET last_active_at = ?, warned_at = NULL WHERE vm_name = ?", (now, vm_name))

    def mark_cleanup_warned(self, vm_name, now):
        _write("UPDATE vm_auto_cleanup SET warned_at = ? WHERE vm_name = ?", (now, vm_name))

    def all_auto_cleanup(self):
        return _rows("SELECT vm_name, inactive_days, last_active_at, warned_at, created_at FROM vm_auto_cleanup")

    # ---- VMs: cloud-init user and keys ----

    def cloudinit(self, vm_name):
        return _one("SELECT username, ssh_keys, updated_at FROM vm_cloudinit WHERE vm_name = ?", (vm_name,))

    def save_cloudinit(self, vm_name, username, ssh_keys, updated_at):
        _write(
            "INSERT INTO vm_cloudinit (vm_name, username, ssh_keys, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET username = excluded.username, ssh_keys = excluded.ssh_keys, "
            "updated_at = excluded.updated_at",
            (vm_name, username, ssh_keys, updated_at),
        )

    def delete_cloudinit(self, vm_name):
        _write("DELETE FROM vm_cloudinit WHERE vm_name = ?", (vm_name,))

    # ---- VMs: start at boot ----

    def boot_setting(self, node, vm_name):
        return _one(
            "SELECT autostart, boot_order, delay_s FROM vm_boot WHERE node = ? AND vm_name = ?", (node, vm_name)
        )

    def set_boot_setting(self, node, vm_name, autostart, order, delay_s):
        _write(
            "INSERT INTO vm_boot (node, vm_name, autostart, boot_order, delay_s) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(node, vm_name) DO UPDATE SET autostart = excluded.autostart, "
            "boot_order = excluded.boot_order, delay_s = excluded.delay_s",
            (node, vm_name, autostart, order, delay_s),
        )

    def delete_boot_setting(self, node, vm_name):
        _write("DELETE FROM vm_boot WHERE node = ? AND vm_name = ?", (node, vm_name))

    def move_boot_setting(self, vm_name, source_node, target_node):
        return (
            _write(
                "UPDATE OR REPLACE vm_boot SET node = ? WHERE node = ? AND vm_name = ?",
                (target_node, source_node, vm_name),
            ).rowcount
            > 0
        )

    def autostart_vms(self, node):
        return _rows("SELECT vm_name, boot_order, delay_s FROM vm_boot WHERE node = ? AND autostart = 1", (node,))

    def claim_boot(self, node, boot_id):
        """True the first time this boot id is recorded for the node."""
        return (
            _write(
                "INSERT INTO vm_boot_state (node, boot_id) VALUES (?, ?) "
                "ON CONFLICT(node) DO UPDATE SET boot_id = excluded.boot_id "
                "WHERE vm_boot_state.boot_id <> excluded.boot_id",
                (node, boot_id),
            ).rowcount
            == 1
        )

    # ---- Containers ----

    def set_container_ssh_user(self, container_name, username):
        _write(
            "INSERT INTO container_ssh_users (container_name, username) VALUES (?, ?) "
            "ON CONFLICT(container_name) DO UPDATE SET username = excluded.username",
            (container_name, username),
        )

    def container_ssh_user(self, container_name):
        row = _one("SELECT username FROM container_ssh_users WHERE container_name = ?", (container_name,))
        return row["username"] if row else None

    def delete_container_ssh_user(self, container_name):
        _write("DELETE FROM container_ssh_users WHERE container_name = ?", (container_name,))

    def set_container_app(self, container_name, image, spec, ip, network):
        _write(
            "INSERT OR REPLACE INTO container_apps (container_name, image, spec, ip, network) VALUES (?, ?, ?, ?, ?)",
            (container_name, image, spec, ip, network),
        )

    def container_app(self, container_name):
        return _one("SELECT * FROM container_apps WHERE container_name = ?", (container_name,))

    def delete_container_app(self, container_name):
        _write("DELETE FROM container_apps WHERE container_name = ?", (container_name,))

    def set_container_storage(self, container_name, pool, base_dir):
        _write(
            "INSERT OR REPLACE INTO container_storage (container_name, pool, base_dir) VALUES (?, ?, ?)",
            (container_name, pool, base_dir),
        )

    def container_storage(self, container_name):
        return _one("SELECT * FROM container_storage WHERE container_name = ?", (container_name,))

    def delete_container_storage(self, container_name):
        _write("DELETE FROM container_storage WHERE container_name = ?", (container_name,))

    # ---- Notes and tags (VMs, containers, nodes) ----

    def meta(self, kind, node, name):
        return _one("SELECT notes, tags FROM object_meta WHERE kind = ? AND node = ? AND name = ?", (kind, node, name))

    def put_meta(self, kind, node, name, notes, tags):
        _write(
            "INSERT INTO object_meta (kind, node, name, notes, tags) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, node, name) DO UPDATE SET notes = excluded.notes, tags = excluded.tags",
            (kind, node, name, notes, tags),
        )

    def delete_meta(self, kind, node, name):
        _write("DELETE FROM object_meta WHERE kind = ? AND node = ? AND name = ?", (kind, node, name))

    def all_meta(self, kind=None):
        if kind:
            return _rows(
                "SELECT kind, node, name, notes, tags FROM object_meta WHERE kind = ? ORDER BY kind, node, name",
                (kind,),
            )
        return _rows("SELECT kind, node, name, notes, tags FROM object_meta ORDER BY kind, node, name")

    def move_vm_meta(self, vm_name, source_node, target_node):
        return (
            _write(
                "UPDATE OR REPLACE object_meta SET node = ? WHERE kind = 'vm' AND node = ? AND name = ?",
                (target_node, source_node, vm_name),
            ).rowcount
            > 0
        )


class SqliteObjectRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteObjectStore()

    async def meta(self, kind, node, name):
        return await asyncio.to_thread(self.sync.meta, kind, node, name)

    async def all_meta(self, kind=None):
        return await asyncio.to_thread(self.sync.all_meta, kind)

    async def boot_setting(self, node, vm_name):
        return await asyncio.to_thread(self.sync.boot_setting, node, vm_name)
