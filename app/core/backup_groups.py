"""Grouped backup jobs: one schedule for many VMs of this node.

A job selects every VM, the VMs carrying a tag (app/core/object_meta.py) or the members of a pool
(app/core/permissions.py), minus the VMs it excludes. The selection is resolved at each run, so a VM created or
tagged later is covered without editing the job. Each VM is backed up by the same code as a manual backup
(app/core/backups.py), one after another, and its retention is the job's policy unless the VM has a schedule of its
own, whose policy then stays in charge (a VM never has two policies deleting each other's backups).

Backups only cover the VMs of the node that runs Hyperlite: that is where backups are made (a remote node's VMs
are backed up by the Hyperlite of that node).
"""

import json
import logging
import re
import threading
from pathlib import PurePosixPath

from app.core import backup_retention, object_meta, paths, permissions, vm_locks
from app.core.libvirt_utils import open_conn

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FREQUENCIES = ("daily", "weekly", "monthly")
# Where a backup must never be written: the service runs as root, a target is a path typed by a user.
FORBIDDEN_ROOTS = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root/.ssh",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var/lib/hyperlite",
)
_running = set()
_running_lock = threading.Lock()


class GroupError(ValueError):
    """A job definition refused, with a message for the user."""


def validate_target(path):
    """An absolute directory outside the system's own directories, without `..`."""
    if not path:
        return None
    p = PurePosixPath(path)
    if not p.is_absolute() or ".." in p.parts or "\x00" in path:
        raise GroupError("The backup directory must be an absolute path without '..'")
    normalized = paths.current(str(p))
    default = str(paths.BACKUPS)
    if normalized == default:
        # The default directory is stored empty, which every version reads as its own: a full path would send a node
        # still on 1.0 (rolling update) to the directory of 1.1.
        return None
    if normalized.startswith(default + "/"):
        return normalized
    if normalized == "/" or any(normalized == root or normalized.startswith(root + "/") for root in FORBIDDEN_ROOTS):
        raise GroupError(f"Backups cannot be written in {normalized}")
    return normalized


def validate(payload):

    name = (payload.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise GroupError("Invalid name: letters, digits, spaces, dots, dashes, 64 characters at most")
    selection = payload.get("selection")
    value = (payload.get("value") or "").strip() or None
    if selection not in ("all", "tag", "pool"):
        raise GroupError("selection must be all, etiquette or pool")
    if selection == "tag":
        tags = object_meta.normalize_tags([value or ""])
        if not tags:
            raise GroupError("Choose the tag whose VMs are backed up")
        value = tags[0]
    elif selection == "pool":
        if not value or not value.isdigit() or int(value) not in {p["id"] for p in permissions.list_pools()}:
            raise GroupError("Choose an existing pool")
    else:
        value = None
    excluded = sorted({str(v) for v in payload.get("excluded") or [] if v})
    if payload.get("frequency") not in FREQUENCIES:
        raise GroupError("Invalid frequency")
    if not TIME_RE.match(payload.get("time") or ""):
        raise GroupError("Invalid time (expected HH:MM)")
    target = validate_target(payload.get("target_dir")) or ""  # the default directory (validate_target)
    store = payload.get("store") or None
    if store:
        from app.core import backup_stores

        if target:
            raise GroupError("Choose a backup store or a directory, not both")
        try:
            backup_stores.get(store)
        except backup_stores.StoreError as e:
            raise GroupError(str(e)) from None
    policy = {}
    for key, low, high in (
        ("retention_count", 1, 365),
        ("keep_days", 1, 366),
        ("keep_weeks", 1, 260),
        ("keep_months", 1, 120),
    ):
        v = payload.get(key)
        if v is None and key != "retention_count":
            policy[key] = None
            continue
        if not isinstance(v, int) or not low <= v <= high:
            raise GroupError(f"{key} must be between {low} and {high}")
        policy[key] = v
    return {
        "name": name,
        "selection": selection,
        "value": value,
        "excluded": excluded,
        "frequency": payload["frequency"],
        "time": payload["time"],
        "target_dir": target,
        "store": store,
        "active": bool(payload.get("active", True)),
        **policy,
    }


def _row(row):
    d = dict(row)
    d["excluded"] = json.loads(d["excluded"] or "[]")
    d["active"] = bool(d["active"])
    d["running"] = d["id"] in _running
    return d


def _store():
    # The backup repository's synchronous bridge: grouped jobs run in the scheduler thread.
    from app.repositories import registry

    return registry.backups().sync


def list_jobs():
    rows = _store().list_group_jobs()
    local = _local_vm_names()
    return [{**_row(r), "vms": resolve(_row(r), local)} for r in rows]


def get_job(job_id):
    row = _store().get_group_job(job_id)
    return _row(row) if row else None


COLS = (
    "name",
    "selection",
    "value",
    "excluded",
    "frequency",
    "time",
    "target_dir",
    "retention_count",
    "keep_days",
    "keep_weeks",
    "keep_months",
    "active",
    "store",
)


def save_job(payload, job_id=None):
    from app.core.backups import _next_run

    clean = validate(payload)
    next_run = _next_run(clean["frequency"], clean["time"]).isoformat()
    if _store().group_name_taken(clean["name"], job_id):
        raise GroupError(f"A backup job named '{clean['name']}' already exists")
    job_id = _store().save_group_job([clean[c] for c in COLS], next_run, job_id)
    return get_job(job_id)


def delete_job(job_id):
    return _store().delete_group_job(job_id)


def _local_vm_names():
    conn = open_conn()
    try:
        return sorted(d.name() for d in conn.listAllDomains())
    finally:
        conn.close()


def resolve(job, local_names=None):
    """The VMs of this node the job covers right now, in name order."""
    names = local_names if local_names is not None else _local_vm_names()
    if job["selection"] == "tag":
        tagged = {
            m["name"] for m in object_meta.list_all("vm") if m["node"] in (None, "local") and job["value"] in m["tags"]
        }
        names = [n for n in names if n in tagged]
    elif job["selection"] == "pool":
        pool = next((p for p in permissions.list_pools() if str(p["id"]) == str(job["value"])), None)
        members = set(pool["vms"]) if pool else set()
        names = [n for n in names if n in members]
    excluded = set(job["excluded"])
    return [n for n in names if n not in excluded]


def run_job(job, username="scheduler"):
    """Back up each VM of the job in turn; returns {vm: "ok" | error message}. A VM another operation holds is
    reported and skipped, the others go on."""
    from app.core.backups import _apply_retention, run_backup

    with _running_lock:
        if job["id"] in _running:
            return {}
        _running.add(job["id"])
    results = {}
    try:
        policy = backup_retention.policy_of(job)
        for vm in resolve(job):
            try:
                backup_id = run_backup(vm, job["target_dir"], username=username, store=job.get("store"))
            except vm_locks.VmBusy as e:
                results[vm] = f"skipped: {e}"
                continue
            except Exception as e:
                results[vm] = str(e) or type(e).__name__
                continue
            _store().set_group(backup_id, job["id"])
            own = _store().get_schedule(vm)
            if not own:
                _apply_retention(vm, policy, _backup_task(backup_id))
            results[vm] = "ok"
    finally:
        with _running_lock:
            _running.discard(job["id"])
    return results


def run_due(now):
    """Called by the backup scheduler: runs the jobs whose time has come, then sets their next run."""
    from app.core import cluster_lead
    from app.core.audit import log_action
    from app.core.backups import _next_run

    own = cluster_lead.in_cluster()  # in a cluster, each node runs the job for its VMs on its own schedule
    for row in _store().due_group_jobs(now.isoformat()):
        job = _row(row)
        results = run_job(job)
        failed = {vm: r for vm, r in results.items() if r != "ok"}
        log_action(
            "scheduler",
            "backup_group",
            job["name"],
            "failed" if failed else "success",
            "; ".join(f"{vm}: {r}" for vm, r in failed.items())[:500] or f"{len(results)} VMs",
        )
        _store().record_group_run(
            job["id"],
            now.isoformat(),
            _next_run(job["frequency"], job["time"], now).isoformat(),
            shared_next=row["next_shared"] if own else None,
        )


def _backup_task(backup_id):
    """The task a backup ran in: its retention is applied in that task's name (backup.delete's needs)."""
    from app.repositories import registry

    return (registry.backups().sync.get(backup_id) or {}).get("task_id")
