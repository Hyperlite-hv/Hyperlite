"""Native VM backup endpoints. The actual logic (qemu-img, transient
external snapshot, scheduling) lives in app/core/backups.py; this file only
validates input, checks permissions and orchestrates the background task."""

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import maintenance, site_recovery, vm_locks
from app.core.audit import log_action
from app.core.backups import (
    DEFAULT_BACKUP_DIR,
    _next_run,
    refuse_vm_with_block_disks,
    restore_backup,
    run_backup,
    verify_backup,
)
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task
from app.core.vm_builder import validate_name
from app.services import backup_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backups"])


@router.get("/backups")
async def list_all_backups(user: dict = Depends(get_current_user)):
    return await backup_service.list_all()


class RecoveryItem(BaseModel):
    chemin: str = Field(max_length=4096)
    nom: str = Field(max_length=128)


class RecoveryRequest(BaseModel):
    elements: list[RecoveryItem] = Field(max_length=site_recovery.MAX_VMS)
    reseau: str | None = Field(None, max_length=64)


@router.get("/backups/site-recovery/scan")
def scan_foreign_backups(chemin: str, user: dict = Depends(require_role("admin"))):
    """The backups another site wrote under `chemin` (an NFS share of this site, for instance), per VM."""
    try:
        return site_recovery.scan(chemin)
    except site_recovery.RecoveryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.post("/backups/site-recovery", status_code=202)
def start_site_recovery(payload: RecoveryRequest, user: dict = Depends(require_role("admin"))):
    """Restore the chosen backups as new VMs of this node, one after another, in one task."""
    try:
        task_id = site_recovery.recover(
            [item.model_dump() for item in payload.elements], payload.reseau or None, user["username"]
        )
    except site_recovery.RecoveryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"task_id": task_id}


@router.get("/backup-schedules")
async def list_backup_schedules(user: dict = Depends(get_current_user)):
    """Every scheduled backup, so a list page can tell which VMs have none."""
    return await backup_service.list_schedules()


@router.get("/vms/{name}/backups")
async def list_vm_backups(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    return await backup_service.list_for_vm(name)


class BackupRequest(BaseModel):
    target_dir: str | None = None


@router.post("/vms/{name}/backups", status_code=202)
def create_backup(name: str, payload: BackupRequest, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    refuse_vm_with_block_disks(name, "A backup")

    # Reuses the vm.snapshot privilege (protecting a VM's state, same spirit)
    # rather than introducing yet another dedicated privilege.
    claim = vm_locks.claim_or_409(name, "a backup")

    def job():
        try:
            run_backup(name, payload.target_dir, username=user["username"], claim=claim)
        except Exception:
            logger.debug(
                "Ignored exception in job()", exc_info=True
            )  # already logged and tracked in run_backup (task + audit_log)
        finally:
            claim.release()  # run_backup releases it too; a no-op then, a safety net if it failed before

    try:
        threading.Thread(target=job, daemon=True).start()
    except BaseException:
        claim.release()
        raise
    log_action(user["username"], "backup_vm_requested", name, "succes")
    return {"message": f"Backup of '{name}' started in the background"}


@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: int, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if not backup_service.find(backup_id):
        raise HTTPException(status_code=404, detail="Backup not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")
    try:
        row = backup_service.delete(backup_id)
    except backup_service.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found") from None
    log_action(user["username"], "delete_backup", row["vm_name"], "succes", f"backup #{backup_id}")
    return {"message": "Backup deleted"}


@router.post("/backups/{backup_id}/verify", status_code=202)
def verify_backup_endpoint(backup_id: int, user: dict = Depends(require_role("admin"))):
    """Recompute every checksum and check the images now, as a task (a large backup takes a while to read)."""
    row = backup_service.find(backup_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    if row["statut"] != "termine":
        raise HTTPException(status_code=409, detail="Only a finished backup can be verified")
    task_id = create_task("verify_backup", row["vm_name"], username=user["username"])

    def job():
        try:
            verify_backup(backup_id, username=user["username"], task_id=task_id)
            finish_task(task_id, "termine")
        except Exception as e:
            logger.warning("Verification of backup #%s failed", backup_id, exc_info=True)
            finish_task(task_id, "echec", str(e))

    threading.Thread(target=job, daemon=True).start()
    return {"task_id": task_id, "backup": backup_id}


class RestoreRequest(BaseModel):
    mode: str = Field(description="'overwrite' (replaces the original VM) or 'new' (new VM)")
    new_name: str | None = None


@router.post("/backups/{backup_id}/restore", status_code=202)
def restore_backup_endpoint(backup_id: int, payload: RestoreRequest, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance("local", "Restoring a backup")
    row = backup_service.find(backup_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    # What can be refused at once is refused here, so a request that cannot run never reads as started.
    if payload.mode not in ("overwrite", "new"):
        raise HTTPException(status_code=422, detail="Invalid mode (expected 'overwrite' or 'new')")
    if row["statut"] != "termine":
        raise HTTPException(status_code=409, detail="This backup is not in a restorable state (failed or in progress)")
    if payload.mode == "new":
        name_error = validate_name(payload.new_name or "")
        if name_error:
            raise HTTPException(status_code=422, detail=name_error)
    target = row["vm_name"] if payload.mode == "overwrite" else payload.new_name
    claim = vm_locks.claim_or_409(target, "a backup restore")

    def job():
        try:
            restore_backup(backup_id, payload.mode, payload.new_name, username=user["username"], claim=claim)
        except Exception:
            logger.debug("Ignored exception in job()", exc_info=True)  # already logged in restore_backup
        finally:
            claim.release()

    try:
        threading.Thread(target=job, daemon=True).start()
    except BaseException:
        claim.release()
        raise
    log_action(user["username"], "restore_backup_requested", row["vm_name"], "succes", f"mode={payload.mode}")
    return {"message": "Restore started in the background"}


class ScheduleRequest(BaseModel):
    frequence: str = Field(description="'quotidien' | 'hebdomadaire' | 'mensuel'")
    heure: str = Field(description="Heure locale UTC au format HH:MM")
    cible_dir: str | None = None
    retention_count: int = Field(7, ge=1, le=365)
    # GFS retention on top of the count (app/core/backup_retention.py); None: not used.
    garder_jours: int | None = Field(None, ge=1, le=366)
    garder_semaines: int | None = Field(None, ge=1, le=260)
    garder_mois: int | None = Field(None, ge=1, le=120)


@router.get("/vms/{name}/backup-schedule")
async def get_backup_schedule(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    return await backup_service.get_schedule(name)


@router.put("/vms/{name}/backup-schedule")
def set_backup_schedule(name: str, payload: ScheduleRequest, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    if payload.frequence not in ("quotidien", "hebdomadaire", "mensuel"):
        raise HTTPException(status_code=422, detail="Invalid frequency")
    try:
        hh, mm = payload.heure.split(":")
        valid_time = 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except ValueError:
        valid_time = False
    if not valid_time:
        raise HTTPException(status_code=422, detail="Invalid time (expected HH:MM)")

    from app.core.backup_groups import GroupError, validate_target

    try:
        target = validate_target(payload.cible_dir) or str(DEFAULT_BACKUP_DIR)
    except GroupError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    next_run = _next_run(payload.frequence, payload.heure)
    backup_service.set_schedule(
        name,
        payload.frequence,
        payload.heure,
        target,
        payload.retention_count,
        payload.garder_jours,
        payload.garder_semaines,
        payload.garder_mois,
        next_run.isoformat(),
    )
    log_action(user["username"], "set_backup_schedule", name, "succes", f"{payload.frequence} at {payload.heure}")
    return backup_service.schedule_of(name)


@router.delete("/vms/{name}/backup-schedule")
def delete_backup_schedule(name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    backup_service.delete_schedule(name)
    log_action(user["username"], "delete_backup_schedule", name, "succes")
    return {"message": "Schedule deleted"}
