"""Backups as the API manages them: the catalogue of VM backups, their schedules, deletion, and container backups.
Running, verifying and restoring backups stays in app/core/backups.py (long operations in threads)."""

import shutil
from pathlib import Path

from app.repositories import registry


class BackupNotFound(LookupError):
    pass


async def list_all():
    return await registry.backups().list_all()


async def list_for_vm(vm_name):
    return await registry.backups().list_for_vm(vm_name)


async def get(backup_id):
    row = await registry.backups().get(backup_id)
    if not row:
        raise BackupNotFound(backup_id)
    return row


def find(backup_id):
    """The catalogue entry or None, for the synchronous endpoints that start threads."""
    return registry.backups().sync.get(backup_id)


def schedule_of(vm_name):
    return registry.backups().sync.get_schedule(vm_name)


def delete(backup_id):
    """Remove the backup's files, then its catalogue entry; returns the entry. Runs in a worker thread."""
    store = registry.backups().sync
    row = store.get(backup_id)
    if not row:
        raise BackupNotFound(backup_id)
    shutil.rmtree(row["chemin"], ignore_errors=True)
    store.delete(backup_id)
    return row


async def list_schedules():
    return await registry.backups().list_schedules()


async def get_schedule(vm_name):
    return await registry.backups().get_schedule(vm_name)


def set_schedule(vm_name, frequence, heure, cible_dir, retention_count, jours, semaines, mois, next_run):
    registry.backups().sync.upsert_schedule(
        vm_name, frequence, heure, cible_dir, retention_count, jours, semaines, mois, next_run
    )


def delete_schedule(vm_name):
    registry.backups().sync.delete_schedule(vm_name)


# ---- Container backups (a tar archive of a stopped container's filesystem) ----


async def list_container_backups():
    return await registry.backups().list_container_backups()


def container_store():
    """The synchronous bridge for the backup thread, which records its progress as it goes."""
    return registry.backups().sync


def get_container_backup(backup_id):
    return registry.backups().sync.get_container_backup(backup_id)


def delete_container_backup(backup_id):
    """Remove the archive and its catalogue entry; returns the entry, None when unknown."""
    store = registry.backups().sync
    row = store.get_container_backup(backup_id)
    if row:
        Path(row["chemin"]).unlink(missing_ok=True)
        store.delete_container_backup(backup_id)
    return row
