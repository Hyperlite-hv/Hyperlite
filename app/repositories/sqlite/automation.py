"""Automation jobs in SQLite: `jobs`, `job_steps`, `job_runs`, `job_run_logs`. Runs execute in threads
(app/core/jobs.py): they use `.sync`."""

import asyncio
import sqlite3

from app.core.database import get_conn
from app.domain.common import AlreadyExists


def _job_with_steps(db, job):
    d = dict(job)
    d["steps"] = [dict(s) for s in db.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY ordre", (job["id"],))]
    return d


class SqliteAutomationStore:
    def list_jobs(self):
        with get_conn() as db:
            return [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY name").fetchall()]

    def get_job(self, job_id):
        """The job with its ordered steps, or None."""
        with get_conn() as db:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return _job_with_steps(db, job) if job else None

    def create_job(self, name, description, username, created_at, steps):
        """steps: dicts with cible_type, cible, commande, condition_type, condition_valeur. Returns the id."""
        with get_conn() as db:
            try:
                cur = db.execute(
                    "INSERT INTO jobs (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (name, description, username, created_at),
                )
            except sqlite3.IntegrityError as e:
                raise AlreadyExists(f"A job '{name}' already exists") from e
            job_id = cur.lastrowid
            for i, s in enumerate(steps):
                db.execute(
                    "INSERT INTO job_steps (job_id, ordre, cible_type, cible, commande, condition_type, condition_valeur) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, i, s["cible_type"], s["cible"], s["commande"], s["condition_type"], s["condition_valeur"]),
                )
            db.commit()
        return job_id

    def delete_job(self, job_id):
        with get_conn() as db:
            db.execute("DELETE FROM job_steps WHERE job_id = ?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            db.commit()

    def ensure_predefined(self, key, name, description, created_at):
        """Create the predefined job once; an existing one gets the current name and description. Returns its id."""
        with get_conn() as db:
            existing = db.execute("SELECT id FROM jobs WHERE predefined_key = ?", (key,)).fetchone()
            if existing:
                db.execute(
                    "UPDATE jobs SET name = ?, description = ? WHERE id = ?", (name, description, existing["id"])
                )
                db.commit()
                return existing["id"]
            cur = db.execute(
                "INSERT INTO jobs (name, description, predefined_key, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, description, key, "system", created_at),
            )
            db.commit()
            return cur.lastrowid

    def create_run(self, run_id, job_id, task_id, dry_run, targets_json, started_at):
        with get_conn() as db:
            db.execute(
                "INSERT INTO job_runs (id, job_id, task_id, dry_run, targets, statut, started_at) "
                "VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
                (run_id, job_id, task_id, int(dry_run), targets_json, started_at),
            )
            db.commit()

    def log_step(self, run_id, step_ordre, cible, commande, stdout, stderr, exit_code, reussi, at):
        with get_conn() as db:
            db.execute(
                "INSERT INTO job_run_logs (run_id, step_ordre, cible, commande, stdout, stderr, exit_code, reussi, horodatage) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, step_ordre, cible, commande, stdout, stderr, exit_code, int(reussi), at),
            )
            db.commit()

    def close_run(self, run_id, statut, finished_at, resultat):
        with get_conn() as db:
            db.execute(
                "UPDATE job_runs SET statut = ?, finished_at = ?, resultat = ? WHERE id = ?",
                (statut, finished_at, resultat, run_id),
            )
            db.commit()

    def list_runs(self, job_id, limit=100):
        with get_conn() as db:
            rows = db.execute(
                "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?", (job_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id):
        """The run with its step logs, or None."""
        with get_conn() as db:
            run = db.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                return None
            d = dict(run)
            d["logs"] = [
                dict(r) for r in db.execute("SELECT * FROM job_run_logs WHERE run_id = ? ORDER BY id", (run_id,))
            ]
        return d


class SqliteAutomationRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteAutomationStore()

    async def list_jobs(self):
        return await asyncio.to_thread(self.sync.list_jobs)

    async def get_job(self, job_id):
        return await asyncio.to_thread(self.sync.get_job, job_id)

    async def create_job(self, name, description, username, created_at, steps):
        return await asyncio.to_thread(self.sync.create_job, name, description, username, created_at, steps)

    async def delete_job(self, job_id):
        await asyncio.to_thread(self.sync.delete_job, job_id)

    async def list_runs(self, job_id, limit=100):
        return await asyncio.to_thread(self.sync.list_runs, job_id, limit)

    async def get_run(self, run_id):
        return await asyncio.to_thread(self.sync.get_run, run_id)
