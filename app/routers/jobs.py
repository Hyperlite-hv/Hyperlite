"""Automation job engine endpoints (Automation tab). The execution logic
lives in app/core/jobs.py."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import renaming
from app.core.audit import log_action
from app.core.jobs import JobRunRefused, start_job_run
from app.core.security import get_current_user, require_role
from app.services import automation_service as automation

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStep(BaseModel):
    cible_type: str = Field(description="'vm' | 'host' | 'chaque_cible' (one target per VM supplied to the run)")
    cible: str | None = None
    commande: str
    condition_type: str = "exit_code"
    condition_valeur: str | None = "0"


class JobCreate(BaseModel):
    name: str
    description: str | None = None
    steps: list[JobStep]


@router.get("")
async def list_jobs(user: dict = Depends(get_current_user)):
    return await automation.list_jobs()


@router.get("/{job_id}")
async def get_job(job_id: int, user: dict = Depends(get_current_user)):
    try:
        return await automation.get_job(job_id)
    except automation.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found") from None


@router.post("", status_code=201)
async def create_job(payload: JobCreate, user: dict = Depends(require_role("admin"))):
    try:
        job = await automation.create_job(
            payload.name, payload.description, [s.model_dump() for s in payload.steps], user["username"]
        )
    except automation.JobInvalid as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except automation.JobExists as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    log_action(user["username"], "create_job", payload.name, "succes")
    return job


class JobRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/{job_id}")
def rename_job(job_id: int, payload: JobRename, user: dict = Depends(require_role("admin"))):
    """Only its name changes: the job (its steps and runs) is known by its id everywhere else."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name required")
    try:
        old = renaming.rename_label("job", job_id, name)
    except renaming.LabelTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if old is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "rename_job", old, "succes", f"-> {name}")
    return {"id": job_id, "name": name}


@router.delete("/{job_id}")
async def delete_job(job_id: int, user: dict = Depends(require_role("admin"))):
    try:
        name = await automation.delete_job(job_id)
    except automation.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found") from None
    except automation.JobProtected as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    log_action(user["username"], "delete_job", name, "succes")
    return {"message": "Job deleted"}


class RunRequest(BaseModel):
    targets: list[str] = []
    dry_run: bool = False


@router.post("/{job_id}/run", status_code=202)
async def run_job_endpoint(job_id: int, payload: RunRequest, user: dict = Depends(require_role("admin"))):
    try:
        job = await automation.get_job(job_id)
    except automation.JobNotFound:
        raise HTTPException(status_code=404, detail="Job not found") from None

    details = f"cibles={payload.targets} dry_run={payload.dry_run}"
    try:
        run_id = await run_in_threadpool(start_job_run, job_id, payload.targets, payload.dry_run, user["username"])
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    except JobRunRefused as e:
        log_action(user["username"], "run_job_requested", job["name"], "echec", f"{details} : {e}")
        raise HTTPException(status_code=422, detail=str(e)) from None
    # Audited only now that the run exists: its outcome is audited separately
    # ("run_job") when it ends.
    log_action(user["username"], "run_job_requested", job["name"], "succes", f"{details} run={run_id}")
    return {"message": f"Run of '{job['name']}' started in the background", "run_id": run_id}


@router.get("/{job_id}/runs")
async def list_job_runs(job_id: int, user: dict = Depends(get_current_user)):
    return await automation.list_runs(job_id)


@router.get("/runs/{run_id}")
async def get_job_run(run_id: str, user: dict = Depends(get_current_user)):
    try:
        return await automation.get_run(run_id)
    except automation.RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found") from None
