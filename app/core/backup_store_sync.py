"""Copies of a backup store into another (docs/design/backup-store.md, section 7; step 4), as Proxmox Backup Server's
sync jobs: the copy's side pulls, on its own schedule, the backups it lacks, chunk by chunk, only the chunks it lacks,
as they are (encrypted ones never decrypted: the copy's side needs no key). The copy keeps its own retention: a backup
deleted at the source, by its retention or by hand, stays in the copy until the copy's own policy lets it go
(Antho, 2026-10-24: a deletion at the source, accidental or malicious, never reaches the copy).

This first step copies between two stores of this cluster: the target on a pool at the other site (an NFS share
there, for instance). A store made as the copy of another (backup_stores.create, copy_of) shares its chunk size and
its key, so the chunks keep their names.

A job runs on the node that leads the cluster's work (cluster_lead), which must reach both stores.
"""

import logging
import re
import threading
from datetime import UTC, datetime

from app.core import backup_retention, backup_stores

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FREQUENCIES = ("daily", "weekly", "monthly")

_running = set()
_running_lock = threading.Lock()


class SyncError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message, self.status = message, status


def _repo():
    from app.repositories import registry

    return registry.backups().sync


def _now():
    return datetime.now(UTC)


def validate(payload):
    name = (payload.get("name") or "").strip()
    if not NAME.match(name):
        raise SyncError("Invalid name: letters, digits, '.', '-' and '_', 63 characters at most")
    source, target = payload.get("source"), payload.get("target")
    if not source or not target or source == target:
        raise SyncError("Choose a source store and another store to copy it into")
    from app.core import backup_remotes

    try:
        target_row = backup_stores.get(target)
        remote = backup_remotes.split_source(source)
        if remote:  # another cluster's store, "<remote>:<store>"
            backup_remotes.get(remote[0])
        else:
            backup_stores.get(source)
    except (backup_stores.StoreError, backup_remotes.RemoteError) as e:
        raise SyncError(e.message, e.status) from None
    if target_row.get("copy_of") != source:
        raise SyncError(f"The store '{target}' is not a copy of '{source}': make the target as a copy of the source")
    if payload.get("frequency") not in FREQUENCIES:
        raise SyncError("Invalid frequency")
    if not TIME_RE.match(payload.get("time") or ""):
        raise SyncError("Invalid time (expected HH:MM)")
    policy = {}
    for key, low, high in (
        ("retention_count", 1, 3650),
        ("keep_days", 1, 3660),
        ("keep_weeks", 1, 520),
        ("keep_months", 1, 240),
    ):
        value = payload.get(key)
        if value is None and key == "retention_count":
            value = 7  # as a backup schedule's default
        if value is None:
            policy[key] = None
            continue
        if not isinstance(value, int) or not low <= value <= high:
            raise SyncError(f"{key} must be between {low} and {high}")
        policy[key] = value
    return {"name": name, "source": source, "target": target, "frequency": payload["frequency"],
            "time": payload["time"], "active": bool(payload.get("active", True)), **policy}  # fmt: skip


def _row(row):
    return {**row, "active": bool(row["active"]), "running": row["id"] in _running}


def list_jobs():
    return [_row(r) for r in _repo().list_syncs()]


def get_job(job_id):
    row = _repo().get_sync(job_id)
    return _row(row) if row else None


def save_job(payload, job_id=None):
    from app.core.backups import _next_run

    clean = validate(payload)
    if _repo().sync_name_taken(clean["name"], job_id):
        raise SyncError(f"A copy job named '{clean['name']}' already exists", 409)
    next_run = _next_run(clean["frequency"], clean["time"]).isoformat()
    values = [clean[c] if c != "active" else (1 if clean[c] else 0) for c in _repo().SYNC_COLUMNS]
    return get_job(_repo().save_sync(values, next_run, job_id))


def delete_job(job_id):
    return _repo().delete_sync(job_id)


def _created(stamp, manifest):
    return manifest.get("time") or stamp


def run_job(job):
    """Copy what the target's policy keeps and the target lacks, then let go what the policy no longer keeps. The
    policy is applied, per VM, to the backups of both stores together: a backup the target would delete at once is
    never copied. Returns {"copied", "chunks", "removed", "kept"}."""
    with _running_lock:
        if job["id"] in _running:
            raise SyncError(f"The copy job '{job['name']}' is already running", 409)
        _running.add(job["id"])
    try:
        from app.core import backup_remotes

        try:
            remote = backup_remotes.split_source(job["source"])
            source = (
                backup_remotes.RemoteStore(*remote) if remote else backup_stores.open_store(job["source"], raw=True)
            )
            target = backup_stores.open_store(job["target"], raw=True)
        except (backup_stores.StoreError, backup_remotes.RemoteError) as e:
            raise SyncError(e.message, e.status) from None
        policy = backup_retention.policy_of(job)
        here = {(vm, stamp): manifest for vm, stamp, manifest in target.backups()}
        there = {(vm, stamp): manifest for vm, stamp, manifest in source.backups()}
        copied = chunks = removed = 0
        for vm in sorted({vm for vm, _ in here} | {vm for vm, _ in there}):
            candidates = {k: m for k, m in {**there, **here}.items() if k[0] == vm}
            keep = backup_retention.kept(
                [(k, _created(k[1], m)) for k, m in candidates.items()],
                policy["last"], policy["daily"], policy["weekly"], policy["monthly"],
            )  # fmt: skip
            for key in sorted(keep - here.keys()):
                chunks += target.copy_backup_from(source, *key)
                copied += 1
            for key in sorted(here.keys() - keep):
                target.delete_backup(*key)
                removed += 1
        if remote:
            source.close()
        return {"copied": copied, "chunks": chunks, "removed": removed, "kept": len(target.backups())}
    finally:
        with _running_lock:
            _running.discard(job["id"])


def _describe(result):
    return (
        f"{result['copied']} backups copied ({result['chunks']} chunks), {result['removed']} removed by its retention"
    )


def run_now(job, username):
    """run_job, recorded: its result on the job and in the audit log."""
    from app.core.audit import log_action
    from app.core.backups import _next_run

    started = _now()
    try:
        result = run_job(job)
    except Exception as e:
        message = getattr(e, "message", None) or str(e) or type(e).__name__
        # The next run at its time, not at the next tick: a store out of reach would fail every minute.
        next_run = _next_run(job["frequency"], job["time"], started).isoformat()
        _repo().record_sync_run(job["id"], started.isoformat(), next_run, f"failed: {message}"[:500])
        log_action(username, "backup_store_sync", job["name"], "failed", message[:300])
        raise
    next_run = _next_run(job["frequency"], job["time"], started).isoformat()
    _repo().record_sync_run(job["id"], started.isoformat(), next_run, _describe(result))
    log_action(username, "backup_store_sync", job["name"], "success", _describe(result))
    return result


def run_due(now=None):
    """The scheduler's tick: the due jobs, on the node that leads the cluster's work, each in a thread of its own."""
    from app.core import cluster_lead

    if not cluster_lead.is_leader():
        return
    for job in _repo().due_syncs((now or _now()).isoformat()):
        with _running_lock:
            if job["id"] in _running:
                continue

        def run(job=job):
            try:
                run_now(job, "scheduler")
            except Exception:
                logger.info("Copy job %s failed", job["name"], exc_info=True)

        threading.Thread(target=run, daemon=True, name=f"store-sync-{job['id']}").start()
