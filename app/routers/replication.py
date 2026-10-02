"""Replication to another site (app/core/replication.py). Administrators only: a job reaches every VM it selects,
whatever the rights on each one, and its status names them."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import replication
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/replication", tags=["backups"])


class ReplicationJob(BaseModel):
    nom: str = Field(max_length=64)
    selection: str  # "toutes" | "etiquette" | "pool"
    valeur: str | None = Field(None, max_length=128)
    exclues: list[str] = Field(default_factory=list, max_length=2000)
    cible_dir: str = Field(max_length=4096)
    intervalle_minutes: int = replication.DEFAULT_INTERVAL
    actif: bool = True


@router.get("/jobs")
def list_jobs(user: dict = Depends(require_role("admin"))):
    return replication.list_jobs()


@router.post("/jobs", status_code=201)
def create_job(payload: ReplicationJob, user: dict = Depends(require_role("admin"))):
    try:
        job = replication.save_job(payload.model_dump())
    except replication.ReplicationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "create_replication_job", job["nom"], "succes", job["cible_dir"])
    return job


@router.put("/jobs/{job_id}")
def update_job(job_id: int, payload: ReplicationJob, user: dict = Depends(require_role("admin"))):
    try:
        job = replication.save_job(payload.model_dump(), job_id)
    except replication.ReplicationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if job is None:
        raise HTTPException(status_code=404, detail="Replication job not found")
    log_action(user["username"], "update_replication_job", job["nom"], "succes", job["cible_dir"])
    return job


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, user: dict = Depends(require_role("admin"))):
    if not replication.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Replication job not found")
    log_action(user["username"], "delete_replication_job", str(job_id), "succes")
    return {"supprime": True}


@router.post("/jobs/{job_id}/run", status_code=202)
def run_job(job_id: int, user: dict = Depends(require_role("admin"))):
    job = next((j for j in replication.list_jobs() if j["id"] == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail="Replication job not found")
    if job["en_cours"]:
        raise HTTPException(status_code=409, detail="This job is already running")
    replication.start_job(job, user["username"])
    log_action(user["username"], "run_replication_job", job["nom"], "succes")
    return {"message": f"Replication '{job['nom']}' started"}


@router.get("/status")
def status(user: dict = Depends(require_role("admin"))):
    return replication.status()
