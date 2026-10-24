"""Native VM backup endpoints. The actual logic (qemu-img, transient
external snapshot, scheduling) lives in app/core/backups.py; this file only
validates input, checks permissions and orchestrates the background task."""

import logging
import threading

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import backup_store, backup_stores, backup_use, cluster_view, maintenance, site_recovery, twofa, vm_locks
from app.core.audit import log_action
from app.core.backups import (
    DEFAULT_BACKUP_DIR,
    _next_run,
    check_space_for,
    refuse_vm_with_block_disks,
    restore_backup,
    run_backup,
    verify_backup,
)
from app.core.libvirt_utils import open_conn
from app.core.listing import listing
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task
from app.core.vm_builder import validate_name
from app.services import backup_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backups"])


# A cluster's backups are kept by the node that made them, which is the node hosting the VM: the requests about a VM of
# another member, or about a backup it holds, go to that member (app/core/cluster_view.py).
NOT_A_MEMBER = "{node} is not a member of this cluster: its VMs cannot be backed up or restored from this node"
_on_vm_node = cluster_view.forward_to("node", NOT_A_MEMBER)
# A backup id is per node: a holder that is not a member (a node that left the cluster) is refused, never read as this
# node's backup with the same id.
_on_holder = cluster_view.forward_to(
    "on", "{node} is not a member of this cluster: its backups are not reachable from here"
)


@router.get("/backups")
async def list_all_backups(cluster: bool = False, user: dict = Depends(get_current_user)):
    """cluster: every member's backups, each marked with the node that holds it ("on"), and the members that did not
    answer; outside a cluster this node's, in the same shape."""
    rows = await backup_service.list_all()
    if not cluster:
        return listing(rows)
    answers, missing = await run_in_threadpool(cluster_view.gather, user["username"], "/backups", {})
    return listing(cluster_view.merge(rows, answers, "created_at"), unreachable=missing)


class RecoveryItem(BaseModel):
    path: str = Field(max_length=4096)
    name: str = Field(max_length=128)


class RecoveryRequest(BaseModel):
    elements: list[RecoveryItem] = Field(max_length=site_recovery.MAX_VMS)
    network: str | None = Field(None, max_length=64)


@router.get("/backups/site-recovery/scan")
def scan_foreign_backups(path: str, user: dict = Depends(require_role("admin"))):
    """The backups another site wrote under `path` (an NFS share of this site, for instance), per VM."""
    try:
        return listing(site_recovery.scan(path))
    except site_recovery.RecoveryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.post("/backups/site-recovery", status_code=202)
def start_site_recovery(payload: RecoveryRequest, user: dict = Depends(require_role("admin"))):
    """Restore the chosen backups as new VMs of this node, one after another, in one task."""
    try:
        task_id = site_recovery.recover(
            [item.model_dump() for item in payload.elements], payload.network or None, user["username"]
        )
    except site_recovery.RecoveryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"task_id": task_id}


@router.get("/backup-schedules")
async def list_backup_schedules(user: dict = Depends(get_current_user)):
    """Every scheduled backup, so a list page can tell which VMs have none."""
    return listing(await backup_service.list_schedules())


@router.get("/vms/{name}/backups")
async def list_vm_backups(
    name: str, user: dict = Depends(require_vm_privilege("vm.view")), _: None = Depends(_on_vm_node)
):
    return listing(await backup_service.list_for_vm(name))


class BackupRequest(BaseModel):
    target_dir: str | None = None
    # A backup store (GET /backup-stores) instead of a directory: every disk type, only new chunks written.
    store: str | None = None


def _store_for(name, target_dir):
    """The store a backup or a schedule names, checked: it exists and this node reaches it; never with a directory."""
    if not name:
        return None
    if target_dir:
        raise HTTPException(status_code=422, detail="Choose a backup store or a directory, not both")
    try:
        backup_stores.open_store(name)
    except backup_stores.StoreError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None
    return name


def _target_dir(path, user, kept=None):
    """The directory a backup of this request is written in: None (the default one), or a checked one. Backups run
    as root: another directory is for administrators only, a VM's own operator (vm.snapshot) keeps the default (a
    manual backup took any directory, /etc included, until the audit before 1.0.0). `kept`: the directory already
    set by an administrator, which an operator editing the schedule sends back unchanged."""
    from app.core.backup_groups import GroupError, validate_target

    try:
        target = validate_target(path)
    except GroupError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if target and target not in (str(DEFAULT_BACKUP_DIR), kept) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only an administrator can choose the backup directory")
    return target


@router.post("/vms/{name}/backups", status_code=202)
def create_backup(
    name: str,
    payload: BackupRequest,
    user: dict = Depends(require_vm_privilege("vm.snapshot")),
    _: None = Depends(_on_vm_node),
):
    target_dir = _target_dir(payload.target_dir, user)
    store = _store_for(payload.store, target_dir)
    if not store:  # a store takes every disk type, and only what it lacks
        refuse_vm_with_block_disks(name, "A backup")
        try:
            check_space_for(name, target_dir)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None

    # Reuses the vm.snapshot privilege (protecting a VM's state, same spirit)
    # rather than introducing yet another dedicated privilege.
    claim = vm_locks.claim_or_409(name, "a backup")

    def job():
        try:
            run_backup(name, target_dir, username=user["username"], claim=claim, store=store)
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
    log_action(user["username"], "backup_vm_requested", name, "success")
    return {"message": f"Backup of '{name}' started in the background"}


@router.delete("/backups/{backup_id}")
def delete_backup(
    backup_id: int, confirm: bool = False, user: dict = Depends(require_role("admin")), _: None = Depends(_on_holder)
):
    if not backup_service.find(backup_id):
        raise HTTPException(status_code=404, detail="Backup not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")
    try:
        row = backup_service.delete(backup_id)
    except backup_service.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found") from None
    except backup_use.BackupInUse as e:
        log_action(user["username"], "delete_backup", backup_service.find(backup_id)["vm_name"], "failed", str(e))
        raise HTTPException(status_code=409, detail=str(e)) from None
    log_action(user["username"], "delete_backup", row["vm_name"], "success", f"backup #{backup_id}")
    return {"message": "Backup deleted"}


@router.post("/backups/{backup_id}/verify", status_code=202)
def verify_backup_endpoint(backup_id: int, user: dict = Depends(require_role("admin")), _: None = Depends(_on_holder)):
    """Recompute every checksum and check the images now, as a task (a large backup takes a while to read)."""
    row = backup_service.find(backup_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    if row["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Only a finished backup can be verified")
    task_id = create_task("verify_backup", row["vm_name"], username=user["username"])

    def job():
        try:
            verify_backup(backup_id, username=user["username"], task_id=task_id)
            finish_task(task_id, "succeeded")
        except Exception as e:
            logger.warning("Verification of backup #%s failed", backup_id, exc_info=True)
            finish_task(task_id, "failed", str(e))

    threading.Thread(target=job, daemon=True).start()
    return {"task_id": task_id, "backup": backup_id}


class RestoreRequest(BaseModel):
    mode: str = Field(description="'overwrite' (replaces the original VM) or 'new' (new VM)")
    new_name: str | None = None


@router.post("/backups/{backup_id}/restore", status_code=202)
def restore_backup_endpoint(
    backup_id: int,
    payload: RestoreRequest,
    user: dict = Depends(require_role("admin")),
    _: None = Depends(_on_holder),
):
    maintenance.refuse_if_in_maintenance("local", "Restoring a backup")
    row = backup_service.find(backup_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    # What can be refused at once is refused here, so a request that cannot run never reads as started.
    if payload.mode not in ("overwrite", "new"):
        raise HTTPException(status_code=422, detail="Invalid mode (expected 'overwrite' or 'new')")
    if row["status"] != "succeeded":
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
    log_action(user["username"], "restore_backup_requested", row["vm_name"], "success", f"mode={payload.mode}")
    return {"message": "Restore started in the background"}


class ScheduleRequest(BaseModel):
    frequency: str = Field(description="'daily' | 'weekly' | 'monthly'")
    time: str = Field(description="Heure locale UTC au format HH:MM")
    target_dir: str | None = None
    retention_count: int = Field(7, ge=1, le=365)
    # GFS retention on top of the count (app/core/backup_retention.py); None: not used.
    keep_days: int | None = Field(None, ge=1, le=366)
    keep_weeks: int | None = Field(None, ge=1, le=260)
    keep_months: int | None = Field(None, ge=1, le=120)
    store: str | None = None  # a backup store instead of a directory


# A schedule is kept by the node that runs it, the VM's (not replicated): read and removed there, as it is set there.
@router.get("/vms/{name}/backup-schedule")
async def get_backup_schedule(
    name: str, user: dict = Depends(require_vm_privilege("vm.view")), _: None = Depends(_on_vm_node)
):
    return await backup_service.get_schedule(name)


def _vm_exists(name):
    conn = open_conn()
    try:
        conn.lookupByName(name)
        return True
    except libvirt.libvirtError:
        return False
    finally:
        conn.close()


@router.put("/vms/{name}/backup-schedule")
def set_backup_schedule(
    name: str,
    payload: ScheduleRequest,
    user: dict = Depends(require_vm_privilege("vm.snapshot")),
    _: None = Depends(_on_vm_node),
):
    # The scheduler backs up the VMs of this host: a schedule for any other name (a typo) failed every night. In a
    # cluster, the schedule of another member's VM is set on that member (the schedules are shared; it runs them).
    if not _vm_exists(name):
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found on this node")
    if payload.frequency not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="Invalid frequency")
    try:
        hh, mm = payload.time.split(":")
        valid_time = 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except ValueError:
        valid_time = False
    if not valid_time:
        raise HTTPException(status_code=422, detail="Invalid time (expected HH:MM)")

    current = backup_service.schedule_of(name)
    # Empty: the default directory (backup_groups.validate_target).
    target = _target_dir(payload.target_dir, user, current and current.get("target_dir")) or ""
    store = _store_for(payload.store, target)
    next_run = _next_run(payload.frequency, payload.time)
    backup_service.set_schedule(
        name,
        payload.frequency,
        payload.time,
        target,
        payload.retention_count,
        payload.keep_days,
        payload.keep_weeks,
        payload.keep_months,
        next_run.isoformat(),
        store,
    )
    log_action(user["username"], "set_backup_schedule", name, "success", f"{payload.frequency} at {payload.time}")
    return backup_service.schedule_of(name)


@router.delete("/vms/{name}/backup-schedule")
def delete_backup_schedule(
    name: str, user: dict = Depends(require_vm_privilege("vm.snapshot")), _: None = Depends(_on_vm_node)
):
    backup_service.delete_schedule(name)
    log_action(user["username"], "delete_backup_schedule", name, "success")
    return {"message": "Schedule deleted"}


# ---- Backup stores (app/core/backup_stores.py, docs/design/backup-store.md) ----


class StoreCreate(BaseModel):
    name: str = Field(description="The store's name")
    pool: str = Field(description="A storage pool of files (directory, NFS, SMB) of this node")
    directory: str | None = Field(None, description="Its directory inside the pool (default: hyperlite-store)")
    encrypted: bool = Field(False, description="A new store: its chunks and files encrypted, the key shown once")
    master_key: str = Field(
        "none", pattern="^(none|generate|own)$", description="A master recovery key: none, made here (shown once), own"
    )
    master_public_key: str | None = Field(None, description="master_key 'own': the public half, PEM (RSA 3072+)")
    key: str | None = Field(None, description="Taking over an encrypted store: its key's code")
    copy_of: str | None = Field(None, description="A new store made as the copy of this store (copy jobs)")


def _store_error(e):
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("/backup-stores")
def list_backup_stores(user: dict = Depends(get_current_user)):
    """Every store of the cluster, with what this node sees of each (reachable, space, backups)."""
    return listing([backup_stores.describe(r) for r in backup_service.list_stores()])


@router.get("/backup-stores/{name}")
def get_backup_store(name: str, user: dict = Depends(get_current_user)):
    try:
        return backup_stores.describe(backup_stores.get(name))
    except backup_stores.StoreError as e:
        raise _store_error(e) from None


@router.post("/backup-stores", status_code=201)
def create_backup_store(payload: StoreCreate, user: dict = Depends(require_role("admin"))):
    """Make a store in a directory of a storage pool, or take over the store already there. An encrypted store's key,
    and a master recovery key made here, are in this answer only: Hyperlite never shows them again."""
    private = None
    public = payload.master_public_key
    if payload.master_key == "generate":
        private, public = backup_store.new_master_key()
    elif payload.master_key == "none":
        public = None
    elif not public:
        raise HTTPException(status_code=422, detail="Give the master key's public half")
    try:
        row, code = backup_stores.create(
            payload.name,
            payload.pool,
            payload.directory,
            user["username"],
            encrypted=payload.encrypted,
            master_public_key=public,
            key=payload.key,
            copy_of=payload.copy_of,
        )
    except backup_stores.StoreError as e:
        log_action(user["username"], "create_backup_store", payload.name, "failed", e.message)
        raise _store_error(e) from None
    detail = f"pool {payload.pool}" + (", encrypted" if row["encrypted"] else "") + (", master key" if public else "")
    log_action(user["username"], "create_backup_store", payload.name, "success", detail)
    out = backup_stores.describe(row)
    if code:
        out["key_code"] = code
        out["key_qr_svg"] = twofa.qr_code_svg(code)
    if private:
        out["master_private_key"] = private.decode()
    return out


@router.delete("/backup-stores/{name}")
def delete_backup_store(name: str, user: dict = Depends(require_role("admin"))):
    """Forget a store no job uses; its directory and backups stay on the pool."""
    try:
        backup_stores.remove(name)
    except backup_stores.StoreError as e:
        raise _store_error(e) from None
    log_action(user["username"], "delete_backup_store", name, "success")
    return {"message": f"Backup store '{name}' removed; its files stay on the pool"}


@router.post("/backup-stores/{name}/gc", status_code=202)
def collect_store_garbage(name: str, user: dict = Depends(require_role("admin"))):
    """Free the chunks no backup uses any more, in the background (a large store takes a while)."""
    try:
        backup_stores.open_store(name)
    except backup_stores.StoreError as e:
        raise _store_error(e) from None
    task_id = create_task("backup_store_gc", name, node=None, username=user["username"])

    def job():
        try:
            result = backup_stores.collect_garbage(name)
        except Exception as e:
            finish_task(task_id, "failed", str(e))
            log_action(user["username"], "backup_store_gc", name, "failed", str(e))
            return
        finish_task(task_id, "succeeded")
        freed = result["freed_bytes"] / 1024**3
        log_action(user["username"], "backup_store_gc", name, "success", f"{result['removed']} chunks, {freed:.1f} GiB")

    threading.Thread(target=job, daemon=True).start()
    return {"task_id": task_id}


# ---- Copies of a store into another (app/core/backup_store_sync.py) ----


class StoreSync(BaseModel):
    name: str
    source: str = Field(description="The store copied")
    target: str = Field(description="The store made as its copy (copy_of), on a pool at the other site")
    frequency: str = Field(description="'daily' | 'weekly' | 'monthly'")
    time: str = Field(description="HH:MM, UTC")
    retention_count: int = 7
    keep_days: int | None = None
    keep_weeks: int | None = None
    keep_months: int | None = None
    active: bool = True


def _sync_error(e):
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("/backup-store-syncs")
def list_store_syncs(user: dict = Depends(require_role("admin"))):
    from app.core import backup_store_sync

    return listing(backup_store_sync.list_jobs())


@router.get("/backup-store-syncs/{job_id}")
def get_store_sync(job_id: int, user: dict = Depends(require_role("admin"))):
    from app.core import backup_store_sync

    job = backup_store_sync.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Copy job not found")
    return job


@router.post("/backup-store-syncs", status_code=201)
def create_store_sync(payload: StoreSync, user: dict = Depends(require_role("admin"))):
    """A copy of a store into another, pulled on its own schedule, with its own retention."""
    from app.core import backup_store_sync

    try:
        job = backup_store_sync.save_job(payload.model_dump())
    except backup_store_sync.SyncError as e:
        raise _sync_error(e) from None
    log_action(
        user["username"], "create_backup_store_sync", job["name"], "success", f"{job['source']} -> {job['target']}"
    )
    return job


@router.put("/backup-store-syncs/{job_id}")
def update_store_sync(job_id: int, payload: StoreSync, user: dict = Depends(require_role("admin"))):
    from app.core import backup_store_sync

    if not backup_store_sync.get_job(job_id):
        raise HTTPException(status_code=404, detail="Copy job not found")
    try:
        job = backup_store_sync.save_job(payload.model_dump(), job_id)
    except backup_store_sync.SyncError as e:
        raise _sync_error(e) from None
    log_action(user["username"], "update_backup_store_sync", job["name"], "success")
    return job


@router.delete("/backup-store-syncs/{job_id}")
def delete_store_sync(job_id: int, user: dict = Depends(require_role("admin"))):
    """The job only: the copies it made stay in the target store."""
    from app.core import backup_store_sync

    job = backup_store_sync.get_job(job_id)
    if not job or not backup_store_sync.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Copy job not found")
    log_action(user["username"], "delete_backup_store_sync", job["name"], "success")
    return {"message": f"Copy job '{job['name']}' deleted; its copies stay in {job['target']}"}


@router.post("/backup-store-syncs/{job_id}/run", status_code=202)
def run_store_sync(job_id: int, user: dict = Depends(require_role("admin"))):
    """Run a copy job now, in the background (a task)."""
    from app.core import backup_store_sync

    job = backup_store_sync.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Copy job not found")
    if job["running"]:
        raise HTTPException(status_code=409, detail=f"The copy job '{job['name']}' is already running")
    task_id = create_task("backup_store_sync", job["name"], node=None, username=user["username"])

    def work():
        try:
            backup_store_sync.run_now(job, user["username"])
        except Exception as e:
            finish_task(task_id, "failed", getattr(e, "message", None) or str(e))
            return
        finish_task(task_id, "succeeded")

    threading.Thread(target=work, daemon=True).start()
    return {"task_id": task_id}
