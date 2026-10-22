"""Grouped backup jobs (app/core/backup_groups.py). Administrators only: a job reaches every VM it selects,
whatever the rights on each one."""

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import backup_groups
from app.core.audit import log_action
from app.core.listing import item, listing
from app.core.security import require_role

router = APIRouter(prefix="/backup-groups", tags=["backups"])


class GroupJob(BaseModel):
    name: str
    selection: str  # "all" | "tag" | "pool"
    value: str | None = None
    excluded: list[str] = []
    frequency: str
    time: str
    target_dir: str | None = None
    retention_count: int = 7
    keep_days: int | None = None
    keep_weeks: int | None = None
    keep_months: int | None = None
    active: bool = True


@router.get("")
def list_group_jobs(user: dict = Depends(require_role("admin"))):
    return listing(backup_groups.list_jobs())


@router.get("/{job_id}")
def get_group_job(job_id: int, user: dict = Depends(require_role("admin"))):
    """One job, as GET /backup-groups lists it (with the VMs it selects now)."""
    return item(backup_groups.list_jobs(), "id", job_id, "Backup job")


@router.post("", status_code=201)
def create_group_job(payload: GroupJob, user: dict = Depends(require_role("admin"))):
    try:
        job = backup_groups.save_job(payload.model_dump())
    except backup_groups.GroupError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(
        user["username"], "create_backup_group", job["name"], "success", f"{job['selection']} {job['value'] or ''}"
    )
    return job


@router.put("/{job_id}")
def update_group_job(job_id: int, payload: GroupJob, user: dict = Depends(require_role("admin"))):
    if backup_groups.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Backup job not found")
    try:
        job = backup_groups.save_job(payload.model_dump(), job_id)
    except backup_groups.GroupError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "update_backup_group", job["name"], "success")
    return job


@router.delete("/{job_id}")
def delete_group_job(job_id: int, user: dict = Depends(require_role("admin"))):
    if not backup_groups.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Backup job not found")
    log_action(user["username"], "delete_backup_group", str(job_id), "success")
    return {"message": "Backup job deleted (its backups are kept)"}


@router.post("/{job_id}/run", status_code=202)
def run_group_job(job_id: int, user: dict = Depends(require_role("admin"))):
    """Run now, in the background: each VM's backup appears in the tasks as it goes."""
    job = backup_groups.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Backup job not found")
    if job["running"]:
        raise HTTPException(status_code=409, detail="This job is already running")
    vms = backup_groups.resolve(job)
    if not vms:
        raise HTTPException(status_code=422, detail="This job selects no VM of this node right now")

    def work():
        results = backup_groups.run_job(job, username=user["username"])
        failed = {vm: r for vm, r in results.items() if r != "ok"}
        log_action(
            user["username"],
            "backup_group",
            job["name"],
            "failed" if failed else "success",
            "; ".join(f"{vm}: {r}" for vm, r in failed.items())[:500] or f"{len(results)} VMs",
        )

    threading.Thread(target=work, name="backup-group", daemon=True).start()
    return {"vms": vms}
