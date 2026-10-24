"""Backups in SQLite: `backups` (the VM backup catalogue), `backup_jobs` (per-VM schedules), `backup_group_jobs`
(grouped jobs) and `container_backups`. Backups and the scheduler run in threads: they use `.sync`."""

import asyncio
import json

from app.core import paths
from app.core.database import get_conn
from app.repositories.sqlite import schedule_state

GROUP_COLUMNS = (
    "name",
    "selection",
    "value",
    "excluded",
    "frequency",
    "time",
    "target_dir",
    "retention_count",
    "keep_days",
    "keep_weeks",
    "keep_months",
    "active",
    "store",
)


GROUP_KIND = "backup_group"


def _here(row):
    """A row with its paths where this version keeps them (app/core/paths.py): rows written up to 1.0 name
    /root/hyperlite, and an empty target directory is the default one."""
    if "path" in row:
        row["path"] = paths.current(row["path"])
    if "target_dir" in row:
        row["target_dir"] = paths.current(row["target_dir"]) or str(paths.BACKUPS)
    return row


def _with_own_schedule(sql, params=()):
    with get_conn() as db:
        rows = [_here(dict(r)) for r in db.execute(sql, params).fetchall()]
        return schedule_state.overlay(db, GROUP_KIND, rows)


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return _here(dict(row)) if row else None


def _all(sql, params=()):
    with get_conn() as db:
        return [_here(dict(r)) for r in db.execute(sql, params).fetchall()]


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


class SqliteBackupStore:
    # ---- VM backups ----

    def list_all(self, limit=500):
        return _all("SELECT * FROM backups ORDER BY created_at DESC LIMIT ?", (limit,))

    def list_for_vm(self, vm_name):
        return _all("SELECT * FROM backups WHERE vm_name = ? ORDER BY created_at DESC", (vm_name,))

    def get(self, backup_id):
        return _one("SELECT * FROM backups WHERE id = ?", (backup_id,))

    def create(self, vm_name, job_id, path, mode, created_at, task_id, store=None):
        return _write(
            "INSERT INTO backups (vm_name, job_id, path, mode, created_at, status, task_id, store) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
            (vm_name, job_id, path, mode, created_at, task_id, store),
        ).lastrowid

    def set_path(self, backup_id, path):
        """A backup into a store gets its directory once the store named it."""
        _write("UPDATE backups SET path = ? WHERE id = ?", (path, backup_id))

    def find_by_path(self, path):
        return _one("SELECT * FROM backups WHERE path = ?", (path,))

    def known_paths(self):
        return {r["path"] for r in _all("SELECT path FROM backups")}

    def register_imported(self, vm_name, path, mode, created_at, size, checksum, source, store=None):
        """A complete backup found on a storage this node can read, made by another installation. `store`: the
        backup store of this cluster that holds it, for a backup in a store."""
        return _write(
            "INSERT INTO backups (vm_name, path, mode, created_at, status, size_bytes, checksum_sha256, imported_from, "
            "store) VALUES (?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)",
            (vm_name, path, mode, created_at, size, checksum, source, store),
        ).lastrowid

    def mark_done(self, backup_id, size, checksum):
        _write(
            "UPDATE backups SET status = 'succeeded', size_bytes = ?, checksum_sha256 = ? WHERE id = ?",
            (size, checksum, backup_id),
        )

    def mark_failed(self, backup_id, error):
        _write("UPDATE backups SET status = 'failed', error = ? WHERE id = ?", (error, backup_id))

    def set_verification(self, backup_id, status, at, detail):
        _write(
            "UPDATE backups SET verification = ?, verified_at = ?, verification_detail = ? WHERE id = ?",
            (status, at, detail, backup_id),
        )

    def set_group(self, backup_id, group_id):
        _write("UPDATE backups SET group_id = ? WHERE id = ?", (group_id, backup_id))

    def delete(self, backup_id):
        _write("DELETE FROM backups WHERE id = ?", (backup_id,))

    def finished_of(self, vm_name):
        """(id, path, created_at, verification) of the VM's finished backups, for the retention policy: the ones this
        installation made. A backup imported from another site's storage (site recovery) is never counted nor deleted
        by it: it may be the only copy left of that site's data."""
        return _all(
            "SELECT id, path, created_at, verification FROM backups WHERE vm_name = ? AND status = 'succeeded' "
            "AND imported_from IS NULL",
            (vm_name,),
        )

    def next_to_verify(self, older_than):
        """The finished backup verified longest ago (or never), when older than `older_than`; else None."""
        row = _one(
            "SELECT id FROM backups WHERE status = 'succeeded' AND (verified_at IS NULL OR verified_at < ?) "
            "ORDER BY COALESCE(verified_at, '') ASC, created_at ASC LIMIT 1",
            (older_than,),
        )
        return row["id"] if row else None

    # ---- Per-VM schedules ----

    def list_schedules(self):
        return _all("SELECT * FROM backup_jobs ORDER BY vm_name")

    def get_schedule(self, vm_name):
        return _one("SELECT * FROM backup_jobs WHERE vm_name = ?", (vm_name,))

    def upsert_schedule(
        self, vm_name, frequency, time, target_dir, retention_count, jours, semaines, mois, next_run, store=None
    ):
        _write(
            "INSERT INTO backup_jobs (vm_name, frequency, time, target_dir, retention_count, keep_days, "
            "keep_weeks, keep_months, active, next_run, store) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET frequency=excluded.frequency, time=excluded.time, "
            "target_dir=excluded.target_dir, retention_count=excluded.retention_count, keep_days=excluded.keep_days, "
            "keep_weeks=excluded.keep_weeks, keep_months=excluded.keep_months, active=1, "
            "next_run=excluded.next_run, store=excluded.store",
            (vm_name, frequency, time, target_dir, retention_count, jours, semaines, mois, next_run, store),
        )

    def delete_schedule(self, vm_name):
        _write("DELETE FROM backup_jobs WHERE vm_name = ?", (vm_name,))

    def due_schedules(self, now):
        return _all("SELECT * FROM backup_jobs WHERE active = 1 AND next_run <= ?", (now,))

    def record_schedule_run(self, job_id, last, next_run):
        _write(
            "UPDATE backup_jobs SET last_run = ?, next_run = ? WHERE id = ?",
            (last, next_run, job_id),
        )

    # ---- Backup stores (app/core/backup_stores.py) ----

    def list_stores(self):
        return _all("SELECT * FROM backup_stores ORDER BY name")

    def get_store(self, name):
        return _one("SELECT * FROM backup_stores WHERE name = ?", (name,))

    def save_store(self, name, pool, directory, store_id, encrypted, created_by, created_at, master_public_key=None):
        _write(
            "INSERT INTO backup_stores (name, pool, directory, store_id, encrypted, created_by, created_at, "
            "master_public_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, pool, directory, store_id, encrypted, created_by, created_at, master_public_key),
        )

    def delete_store(self, name):
        with get_conn() as db:
            db.execute("DELETE FROM backup_store_keys WHERE name = ?", (name,))
            db.execute("DELETE FROM backup_stores WHERE name = ?", (name,))
            db.commit()

    def set_store_copy_of(self, name, source):
        _write("UPDATE backup_stores SET copy_of = ? WHERE name = ?", (source, name))

    # ---- Other clusters' stores (app/core/backup_remotes.py) ----

    def list_remotes(self):
        return _all("SELECT * FROM backup_remotes ORDER BY name")

    def get_remote(self, name):
        return _one("SELECT * FROM backup_remotes WHERE name = ?", (name,))

    def save_remote(self, name, address, port, fingerprint, token_encrypted, created_by, created_at):
        _write(
            "INSERT INTO backup_remotes (name, address, port, fingerprint, token_encrypted, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, address, port, fingerprint, token_encrypted, created_by, created_at),
        )

    def delete_remote(self, name):
        _write("DELETE FROM backup_remotes WHERE name = ?", (name,))

    # ---- Copies of a store (app/core/backup_store_sync.py) ----

    SYNC_COLUMNS = (
        "name",
        "source",
        "target",
        "frequency",
        "time",
        "retention_count",
        "keep_days",
        "keep_weeks",
        "keep_months",
        "active",
    )

    def list_syncs(self):
        return _all("SELECT * FROM backup_store_syncs ORDER BY name")

    def get_sync(self, job_id):
        return _one("SELECT * FROM backup_store_syncs WHERE id = ?", (job_id,))

    def sync_name_taken(self, name, job_id=None):
        return _one("SELECT id FROM backup_store_syncs WHERE name = ? AND id IS NOT ?", (name, job_id)) is not None

    def save_sync(self, values, next_run, job_id=None):
        """values: SYNC_COLUMNS in order. Returns the id."""
        cols = self.SYNC_COLUMNS
        with get_conn() as db:
            if job_id is None:
                cur = db.execute(
                    f"INSERT INTO backup_store_syncs ({', '.join(cols)}, next_run) VALUES ({', '.join('?' * (len(cols) + 1))})",  # noqa: S608 - fixed columns
                    [*values, next_run],
                )
                job_id = cur.lastrowid
            else:
                db.execute(
                    f"UPDATE backup_store_syncs SET {', '.join(f'{c} = ?' for c in cols)}, next_run = ? WHERE id = ?",  # noqa: S608 - fixed columns
                    [*values, next_run, job_id],
                )
            db.commit()
        return job_id

    def delete_sync(self, job_id):
        return _write("DELETE FROM backup_store_syncs WHERE id = ?", (job_id,)).rowcount > 0

    def due_syncs(self, now):
        return _all("SELECT * FROM backup_store_syncs WHERE active = 1 AND next_run <= ?", (now,))

    def record_sync_run(self, job_id, last, next_run, result):
        _write(
            "UPDATE backup_store_syncs SET last_run = ?, next_run = ?, last_result = ? WHERE id = ?",
            (last, next_run, result, job_id),
        )

    def save_store_key(self, name, key_encrypted, created_at):
        _write(
            "INSERT OR REPLACE INTO backup_store_keys (name, key_encrypted, created_at) VALUES (?, ?, ?)",
            (name, key_encrypted, created_at),
        )

    def get_store_key(self, name):
        row = _one("SELECT key_encrypted FROM backup_store_keys WHERE name = ?", (name,))
        return row["key_encrypted"] if row else None

    # ---- Grouped jobs ----

    def list_group_jobs(self):
        return _with_own_schedule("SELECT * FROM backup_group_jobs ORDER BY name")

    def get_group_job(self, job_id):
        rows = _with_own_schedule("SELECT * FROM backup_group_jobs WHERE id = ?", (job_id,))
        return rows[0] if rows else None

    def group_name_taken(self, name, job_id=None):
        return _one("SELECT id FROM backup_group_jobs WHERE name = ? AND id IS NOT ?", (name, job_id)) is not None

    def save_group_job(self, values, next_run, job_id=None):
        """values: GROUP_COLUMNS in order ("excluded" a list, "active" a bool). Returns the id."""
        row = [
            json.dumps(v) if c == "excluded" else (1 if v else 0) if c == "active" else v
            for c, v in zip(GROUP_COLUMNS, values, strict=True)
        ]
        with get_conn() as db:
            if job_id is None:
                cur = db.execute(
                    f"INSERT INTO backup_group_jobs ({', '.join(GROUP_COLUMNS)}, next_run) "  # noqa: S608
                    f"VALUES ({', '.join('?' * (len(GROUP_COLUMNS) + 1))})",
                    [*row, next_run],
                )
                job_id = cur.lastrowid
            else:
                db.execute(
                    f"UPDATE backup_group_jobs SET {', '.join(f'{c} = ?' for c in GROUP_COLUMNS)}, next_run = ? WHERE id = ?",  # noqa: S608
                    [*row, next_run, job_id],
                )
            db.commit()
        return job_id

    def delete_group_job(self, job_id):
        with get_conn() as db:
            deleted = db.execute("DELETE FROM backup_group_jobs WHERE id = ?", (job_id,)).rowcount > 0
            schedule_state.forget(db, GROUP_KIND, job_id)
            db.commit()
        return deleted

    def due_group_jobs(self, now):
        """The active jobs due on this node; each carries the shared next run as "next_shared"."""
        with get_conn() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM backup_group_jobs WHERE active = 1").fetchall()]
            return schedule_state.due(db, GROUP_KIND, rows, now)

    def record_group_run(self, job_id, last, next_run, shared_next=None):
        """shared_next None: the next run is the shared one (a single node). Otherwise (in a cluster) the next run is
        this node's own, computed from the shared value shared_next."""
        with get_conn() as db:
            if shared_next is None:
                db.execute(
                    "UPDATE backup_group_jobs SET last_run = ?, next_run = ? WHERE id = ?",
                    (last, next_run, job_id),
                )
            else:
                db.execute("UPDATE backup_group_jobs SET last_run = ? WHERE id = ?", (last, job_id))
                schedule_state.record(db, GROUP_KIND, job_id, shared_next, next_run)
            db.commit()

    # ---- Container backups ----

    def list_container_backups(self):
        return _all("SELECT * FROM container_backups ORDER BY created_at DESC")

    def get_container_backup(self, backup_id):
        return _one("SELECT * FROM container_backups WHERE id = ?", (backup_id,))

    def create_container_backup(self, container_name, path, created_at, task_id):
        return _write(
            "INSERT INTO container_backups (container_name, path, created_at, status, task_id) VALUES (?, ?, ?, 'running', ?)",
            (container_name, path, created_at, task_id),
        ).lastrowid

    def container_backup_done(self, backup_id, size):
        _write("UPDATE container_backups SET status = 'succeeded', size_bytes = ? WHERE id = ?", (size, backup_id))

    def container_backup_failed(self, backup_id, error):
        _write("UPDATE container_backups SET status = 'failed', error = ? WHERE id = ?", (error, backup_id))

    def delete_container_backup(self, backup_id):
        _write("DELETE FROM container_backups WHERE id = ?", (backup_id,))


class SqliteBackupRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteBackupStore()

    async def list_all(self, limit=500):
        return await asyncio.to_thread(self.sync.list_all, limit)

    async def list_for_vm(self, vm_name):
        return await asyncio.to_thread(self.sync.list_for_vm, vm_name)

    async def get(self, backup_id):
        return await asyncio.to_thread(self.sync.get, backup_id)

    async def list_schedules(self):
        return await asyncio.to_thread(self.sync.list_schedules)

    async def get_schedule(self, vm_name):
        return await asyncio.to_thread(self.sync.get_schedule, vm_name)

    async def list_container_backups(self):
        return await asyncio.to_thread(self.sync.list_container_backups)
