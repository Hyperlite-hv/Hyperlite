"""Native VM backup endpoints. The actual logic (qemu-img, transient
external snapshot, scheduling) lives in app/core/backups.py; this file only
validates input, checks permissions and orchestrates the background task."""

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import maintenance, vm_locks
from app.core.audit import log_action
from app.core.backups import (
    DEFAULT_BACKUP_DIR,
    _next_run,
    refuse_vm_with_block_disks,
    restore_backup,
    run_backup,
    verify_backup,
)
from app.core.database import get_conn
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task
from app.core.vm_builder import validate_name

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backups"])


@router.get("/backups")
def list_all_backups(user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM backups ORDER BY cree_le DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


@router.get("/backup-schedules")
def list_backup_schedules(user: dict = Depends(get_current_user)):
    """Every scheduled backup, so a list page can tell which VMs have none."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM backup_jobs ORDER BY vm_name").fetchall()
    return [dict(r) for r in rows]


@router.get("/vms/{name}/backups")
def list_vm_backups(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM backups WHERE vm_name = ? ORDER BY cree_le DESC", (name,)).fetchall()
    return [dict(r) for r in rows]


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
    import shutil

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Backup not found")
        if not confirm:
            raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")
        shutil.rmtree(row["chemin"], ignore_errors=True)
        conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
        conn.commit()
    log_action(user["username"], "delete_backup", row["vm_name"], "succes", f"backup #{backup_id}")
    return {"message": "Backup deleted"}


@router.post("/backups/{backup_id}/verify", status_code=202)
def verify_backup_endpoint(backup_id: int, user: dict = Depends(require_role("admin"))):
    """Recompute every checksum and check the images now, as a task (a large backup takes a while to read)."""
    with get_conn() as conn:
        row = conn.execute("SELECT vm_name, statut FROM backups WHERE id = ?", (backup_id,)).fetchone()
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
    with get_conn() as conn:
        row = conn.execute("SELECT vm_name, statut FROM backups WHERE id = ?", (backup_id,)).fetchone()
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
def get_backup_schedule(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM backup_jobs WHERE vm_name = ?", (name,)).fetchone()
    return dict(row) if row else None


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
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backup_jobs (vm_name, frequence, heure, cible_dir, retention_count, garder_jours, "
            "garder_semaines, garder_mois, actif, prochaine_execution) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET frequence=excluded.frequence, heure=excluded.heure, "
            "cible_dir=excluded.cible_dir, retention_count=excluded.retention_count, garder_jours=excluded.garder_jours, "
            "garder_semaines=excluded.garder_semaines, garder_mois=excluded.garder_mois, actif=1, "
            "prochaine_execution=excluded.prochaine_execution",
            (
                name,
                payload.frequence,
                payload.heure,
                target,
                payload.retention_count,
                payload.garder_jours,
                payload.garder_semaines,
                payload.garder_mois,
                next_run.isoformat(),
            ),
        )
        conn.commit()
    log_action(user["username"], "set_backup_schedule", name, "succes", f"{payload.frequence} at {payload.heure}")
    return get_backup_schedule(name, user=user)


@router.delete("/vms/{name}/backup-schedule")
def delete_backup_schedule(name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM backup_jobs WHERE vm_name = ?", (name,))
        conn.commit()
    log_action(user["username"], "delete_backup_schedule", name, "succes")
    return {"message": "Schedule deleted"}
