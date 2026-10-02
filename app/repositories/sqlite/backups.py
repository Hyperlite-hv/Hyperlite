"""Backups in SQLite: `backups` (the VM backup catalogue), `backup_jobs` (per-VM schedules), `backup_group_jobs`
(grouped jobs) and `container_backups`. Backups and the scheduler run in threads: they use `.sync`."""

import asyncio
import json

from app.core.database import get_conn

GROUP_COLUMNS = (
    "nom",
    "selection",
    "valeur",
    "exclues",
    "frequence",
    "heure",
    "cible_dir",
    "retention_count",
    "garder_jours",
    "garder_semaines",
    "garder_mois",
    "actif",
)


def _one(sql, params=()):
    with get_conn() as db:
        row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _all(sql, params=()):
    with get_conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def _write(sql, params=()):
    with get_conn() as db:
        cur = db.execute(sql, params)
        db.commit()
    return cur


class SqliteBackupStore:
    # ---- VM backups ----

    def list_all(self, limit=500):
        return _all("SELECT * FROM backups ORDER BY cree_le DESC LIMIT ?", (limit,))

    def list_for_vm(self, vm_name):
        return _all("SELECT * FROM backups WHERE vm_name = ? ORDER BY cree_le DESC", (vm_name,))

    def get(self, backup_id):
        return _one("SELECT * FROM backups WHERE id = ?", (backup_id,))

    def create(self, vm_name, job_id, chemin, mode, cree_le, task_id):
        return _write(
            "INSERT INTO backups (vm_name, job_id, chemin, mode, cree_le, statut, task_id) VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
            (vm_name, job_id, chemin, mode, cree_le, task_id),
        ).lastrowid

    def find_by_path(self, chemin):
        return _one("SELECT * FROM backups WHERE chemin = ?", (chemin,))

    def known_paths(self):
        return {r["chemin"] for r in _all("SELECT chemin FROM backups")}

    def register_imported(self, vm_name, chemin, mode, cree_le, size, checksum, source):
        """A complete backup found on a storage this node can read, made by another installation."""
        return _write(
            "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut, taille_octets, checksum_sha256, importe_de) "
            "VALUES (?, ?, ?, ?, 'termine', ?, ?, ?)",
            (vm_name, chemin, mode, cree_le, size, checksum, source),
        ).lastrowid

    def mark_done(self, backup_id, size, checksum):
        _write(
            "UPDATE backups SET statut = 'termine', taille_octets = ?, checksum_sha256 = ? WHERE id = ?",
            (size, checksum, backup_id),
        )

    def mark_failed(self, backup_id, error):
        _write("UPDATE backups SET statut = 'echec', erreur = ? WHERE id = ?", (error, backup_id))

    def set_verification(self, backup_id, status, at, detail):
        _write(
            "UPDATE backups SET verification = ?, verifie_le = ?, verification_detail = ? WHERE id = ?",
            (status, at, detail, backup_id),
        )

    def set_group(self, backup_id, group_id):
        _write("UPDATE backups SET groupe_id = ? WHERE id = ?", (group_id, backup_id))

    def delete(self, backup_id):
        _write("DELETE FROM backups WHERE id = ?", (backup_id,))

    def finished_of(self, vm_name):
        """(id, chemin, cree_le) of the VM's finished backups, for the retention policy."""
        return _all("SELECT id, chemin, cree_le FROM backups WHERE vm_name = ? AND statut = 'termine'", (vm_name,))

    def next_to_verify(self, older_than):
        """The finished backup verified longest ago (or never), when older than `older_than`; else None."""
        row = _one(
            "SELECT id FROM backups WHERE statut = 'termine' AND (verifie_le IS NULL OR verifie_le < ?) "
            "ORDER BY COALESCE(verifie_le, '') ASC, cree_le ASC LIMIT 1",
            (older_than,),
        )
        return row["id"] if row else None

    # ---- Per-VM schedules ----

    def list_schedules(self):
        return _all("SELECT * FROM backup_jobs ORDER BY vm_name")

    def get_schedule(self, vm_name):
        return _one("SELECT * FROM backup_jobs WHERE vm_name = ?", (vm_name,))

    def upsert_schedule(self, vm_name, frequence, heure, cible_dir, retention_count, jours, semaines, mois, next_run):
        _write(
            "INSERT INTO backup_jobs (vm_name, frequence, heure, cible_dir, retention_count, garder_jours, "
            "garder_semaines, garder_mois, actif, prochaine_execution) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET frequence=excluded.frequence, heure=excluded.heure, "
            "cible_dir=excluded.cible_dir, retention_count=excluded.retention_count, garder_jours=excluded.garder_jours, "
            "garder_semaines=excluded.garder_semaines, garder_mois=excluded.garder_mois, actif=1, "
            "prochaine_execution=excluded.prochaine_execution",
            (vm_name, frequence, heure, cible_dir, retention_count, jours, semaines, mois, next_run),
        )

    def delete_schedule(self, vm_name):
        _write("DELETE FROM backup_jobs WHERE vm_name = ?", (vm_name,))

    def due_schedules(self, now):
        return _all("SELECT * FROM backup_jobs WHERE actif = 1 AND prochaine_execution <= ?", (now,))

    def record_schedule_run(self, job_id, last, next_run):
        _write(
            "UPDATE backup_jobs SET derniere_execution = ?, prochaine_execution = ? WHERE id = ?",
            (last, next_run, job_id),
        )

    # ---- Grouped jobs ----

    def list_group_jobs(self):
        return _all("SELECT * FROM backup_group_jobs ORDER BY nom")

    def get_group_job(self, job_id):
        return _one("SELECT * FROM backup_group_jobs WHERE id = ?", (job_id,))

    def group_name_taken(self, name, job_id=None):
        return _one("SELECT id FROM backup_group_jobs WHERE nom = ? AND id IS NOT ?", (name, job_id)) is not None

    def save_group_job(self, values, next_run, job_id=None):
        """values: GROUP_COLUMNS in order ("exclues" a list, "actif" a bool). Returns the id."""
        row = [
            json.dumps(v) if c == "exclues" else (1 if v else 0) if c == "actif" else v
            for c, v in zip(GROUP_COLUMNS, values, strict=True)
        ]
        with get_conn() as db:
            if job_id is None:
                cur = db.execute(
                    f"INSERT INTO backup_group_jobs ({', '.join(GROUP_COLUMNS)}, prochaine_execution) "  # noqa: S608
                    f"VALUES ({', '.join('?' * (len(GROUP_COLUMNS) + 1))})",
                    [*row, next_run],
                )
                job_id = cur.lastrowid
            else:
                db.execute(
                    f"UPDATE backup_group_jobs SET {', '.join(f'{c} = ?' for c in GROUP_COLUMNS)}, prochaine_execution = ? WHERE id = ?",  # noqa: S608
                    [*row, next_run, job_id],
                )
            db.commit()
        return job_id

    def delete_group_job(self, job_id):
        return _write("DELETE FROM backup_group_jobs WHERE id = ?", (job_id,)).rowcount > 0

    def due_group_jobs(self, now):
        return _all("SELECT * FROM backup_group_jobs WHERE actif = 1 AND prochaine_execution <= ?", (now,))

    def record_group_run(self, job_id, last, next_run):
        _write(
            "UPDATE backup_group_jobs SET derniere_execution = ?, prochaine_execution = ? WHERE id = ?",
            (last, next_run, job_id),
        )

    # ---- Container backups ----

    def list_container_backups(self):
        return _all("SELECT * FROM container_backups ORDER BY cree_le DESC")

    def get_container_backup(self, backup_id):
        return _one("SELECT * FROM container_backups WHERE id = ?", (backup_id,))

    def create_container_backup(self, container_name, chemin, cree_le, task_id):
        return _write(
            "INSERT INTO container_backups (container_name, chemin, cree_le, statut, task_id) VALUES (?, ?, ?, 'en_cours', ?)",
            (container_name, chemin, cree_le, task_id),
        ).lastrowid

    def container_backup_done(self, backup_id, size):
        _write("UPDATE container_backups SET statut = 'termine', taille_octets = ? WHERE id = ?", (size, backup_id))

    def container_backup_failed(self, backup_id, error):
        _write("UPDATE container_backups SET statut = 'echec', erreur = ? WHERE id = ?", (error, backup_id))

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
