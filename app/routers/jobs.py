"""Endpoints du moteur de Jobs (onglet Automation, chantier 14). La logique
d'execution vit dans app/core/jobs.py."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.jobs import LB_PREDEFINED_KEY, run_job_async, run_lb_job_async
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStep(BaseModel):
    cible_type: str = Field(description="'vm' | 'host' | 'chaque_cible' (une cible par VM fournie au run)")
    cible: str | None = None
    commande: str
    condition_type: str = "exit_code"
    condition_valeur: str | None = "0"


class JobCreate(BaseModel):
    name: str
    description: str | None = None
    steps: list[JobStep]


def _job_summary(job_id):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        steps = conn.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY ordre", (job_id,)).fetchall()
    d = dict(job)
    d["steps"] = [dict(s) for s in steps]
    return d


@router.get("")
def list_jobs(user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@router.get("/{job_id}")
def get_job(job_id: int, user: dict = Depends(get_current_user)):
    job = _job_summary(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable")
    return job


@router.post("", status_code=201)
def create_job(payload: JobCreate, user: dict = Depends(require_role("admin"))):
    if not payload.steps:
        raise HTTPException(status_code=422, detail="Un job doit avoir au moins une étape")
    for s in payload.steps:
        if s.cible_type not in ("vm", "host", "chaque_cible"):
            raise HTTPException(status_code=422, detail="cible_type invalide")
        if s.cible_type == "vm" and not s.cible:
            raise HTTPException(status_code=422, detail="cible requise quand cible_type='vm'")

    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO jobs (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
                (payload.name, payload.description, user["username"], datetime.now(timezone.utc).isoformat()),
            )
        except Exception:
            raise HTTPException(status_code=409, detail=f"Un job '{payload.name}' existe déjà")
        job_id = cur.lastrowid
        for i, s in enumerate(payload.steps):
            conn.execute(
                "INSERT INTO job_steps (job_id, ordre, cible_type, cible, commande, condition_type, condition_valeur) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, i, s.cible_type, s.cible, s.commande, s.condition_type, s.condition_valeur),
            )
        conn.commit()
    log_action(user["username"], "create_job", payload.name, "succes")
    return _job_summary(job_id)


@router.delete("/{job_id}")
def delete_job(job_id: int, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        job = conn.execute("SELECT name, predefined_key FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job introuvable")
        if job["predefined_key"]:
            raise HTTPException(status_code=403, detail="Ce job prédéfini ne peut pas être supprimé")
        conn.execute("DELETE FROM job_steps WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    log_action(user["username"], "delete_job", job["name"], "succes")
    return {"message": "Job supprimé"}


class RunRequest(BaseModel):
    targets: list[str] = []
    dry_run: bool = False


@router.post("/{job_id}/run", status_code=202)
def run_job_endpoint(job_id: int, payload: RunRequest, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Job introuvable")

    if job["predefined_key"] == LB_PREDEFINED_KEY:
        run_lb_job_async(job_id, payload.targets, payload.dry_run, username=user["username"])
    else:
        run_job_async(job_id, payload.targets, payload.dry_run, username=user["username"])
    log_action(user["username"], "run_job_requested", job["name"], "succes", f"cibles={payload.targets} dry_run={payload.dry_run}")
    return {"message": f"Exécution de '{job['name']}' lancée en arrière-plan"}


@router.get("/{job_id}/runs")
def list_job_runs(job_id: int, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT 100", (job_id,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}")
def get_job_run(run_id: str, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Exécution introuvable")
        logs = conn.execute("SELECT * FROM job_run_logs WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    d = dict(run)
    d["logs"] = [dict(l) for l in logs]
    return d
