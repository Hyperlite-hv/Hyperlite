"""Replication to another site in SQLite: `replication_jobs` (what is replicated, where, how often) and
`replication_state` (each VM's current chain and its last copy). The engine runs in threads: it uses `.sync`."""

import asyncio
import json

from app.core.database import get_conn
from app.repositories.sqlite import schedule_state

KIND = "replication"
JOB_COLUMNS = ("nom", "selection", "valeur", "exclues", "cible_dir", "intervalle_minutes", "actif")


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


def _job(row):
    if row:
        row["exclues"] = json.loads(row["exclues"] or "[]")
        row["actif"] = bool(row["actif"])
    return row


def _with_own_schedule(sql, params=()):
    with get_conn() as db:
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
        return [_job(r) for r in schedule_state.overlay(db, KIND, rows)]


class SqliteReplicationStore:
    def list_jobs(self):
        return _with_own_schedule("SELECT * FROM replication_jobs ORDER BY nom")

    def get_job(self, job_id):
        rows = _with_own_schedule("SELECT * FROM replication_jobs WHERE id = ?", (job_id,))
        return rows[0] if rows else None

    def create_job(self, values, next_run):
        cols = ", ".join(JOB_COLUMNS)
        marks = ", ".join("?" for _ in JOB_COLUMNS)
        params = [json.dumps(values[c]) if c == "exclues" else values[c] for c in JOB_COLUMNS]
        return _write(
            f"INSERT INTO replication_jobs ({cols}, prochaine_execution) VALUES ({marks}, ?)",  # noqa: S608 - fixed columns
            (*params, next_run),
        ).lastrowid

    def update_job(self, job_id, values, next_run):
        sets = ", ".join(f"{c} = ?" for c in JOB_COLUMNS)
        params = [json.dumps(values[c]) if c == "exclues" else values[c] for c in JOB_COLUMNS]
        return (
            _write(
                f"UPDATE replication_jobs SET {sets}, prochaine_execution = ? WHERE id = ?",  # noqa: S608 - fixed columns
                (*params, next_run, job_id),
            ).rowcount
            > 0
        )

    def delete_job(self, job_id):
        with get_conn() as db:
            deleted = db.execute("DELETE FROM replication_jobs WHERE id = ?", (job_id,)).rowcount > 0
            schedule_state.forget(db, KIND, job_id)
            db.commit()
        return deleted

    def due_jobs(self, now):
        """The active jobs due on this node; each carries the shared next run as "prochaine_partagee"."""
        with get_conn() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM replication_jobs WHERE actif = 1").fetchall()]
            return [_job(r) for r in schedule_state.due(db, KIND, rows, now)]

    def record_run(self, job_id, ran_at, next_run, shared_next=None):
        """shared_next None: the next run is the shared one (a single node). Otherwise (in a cluster) the next run is
        this node's own, computed from the shared value shared_next."""
        with get_conn() as db:
            if shared_next is None:
                db.execute(
                    "UPDATE replication_jobs SET derniere_execution = ?, prochaine_execution = ? WHERE id = ?",
                    (ran_at, next_run, job_id),
                )
            else:
                db.execute("UPDATE replication_jobs SET derniere_execution = ? WHERE id = ?", (ran_at, job_id))
                schedule_state.record(db, KIND, job_id, shared_next, next_run)
            db.commit()

    def state(self, vm_name):
        return _one("SELECT * FROM replication_state WHERE vm_name = ?", (vm_name,))

    def states(self):
        return _all("SELECT * FROM replication_state ORDER BY vm_name")

    def save_state(self, vm_name, **fields):
        current = self.state(vm_name) or {"vm_name": vm_name, "points": 0, "statut": "jamais"}
        current.update(fields)
        cols = [
            "vm_name",
            "cible_dir",
            "chaine",
            "dernier_point",
            "checkpoint",
            "points",
            "dernier_ok_le",
            "mtime_disques",
            "statut",
            "erreur",
            "maj_le",
        ]
        _write(
            f"INSERT OR REPLACE INTO replication_state ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",  # noqa: S608 - fixed columns
            tuple(current.get(c) for c in cols),
        )

    def forget_chain(self, vm_name):
        """The chain cannot be continued (checkpoints dropped): the next copy starts a new one."""
        _write("UPDATE replication_state SET checkpoint = NULL WHERE vm_name = ?", (vm_name,))

    def rename_vm(self, old, new):
        _write("UPDATE replication_state SET vm_name = ? WHERE vm_name = ?", (new, old))

    def delete_state(self, vm_name):
        _write("DELETE FROM replication_state WHERE vm_name = ?", (vm_name,))


class SqliteReplicationRepository:
    def __init__(self, store=None):
        self.sync = store or SqliteReplicationStore()

    async def list_jobs(self):
        return await asyncio.to_thread(self.sync.list_jobs)

    async def states(self):
        return await asyncio.to_thread(self.sync.states)
