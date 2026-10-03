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

from app.core import backup_retention, object_meta, permissions, vm_locks
from app.core.libvirt_utils import open_conn

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FREQUENCIES = ("quotidien", "hebdomadaire", "mensuel")
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
    normalized = str(p)
    if normalized == "/" or any(normalized == root or normalized.startswith(root + "/") for root in FORBIDDEN_ROOTS):
        raise GroupError(f"Backups cannot be written in {normalized}")
    return normalized


def validate(payload):
    from app.core.backups import DEFAULT_BACKUP_DIR

    name = (payload.get("nom") or "").strip()
    if not NAME_RE.match(name):
        raise GroupError("Invalid name: letters, digits, spaces, dots, dashes, 64 characters at most")
    selection = payload.get("selection")
    value = (payload.get("valeur") or "").strip() or None
    if selection not in ("toutes", "etiquette", "pool"):
        raise GroupError("selection must be toutes, etiquette or pool")
    if selection == "etiquette":
        tags = object_meta.normalize_tags([value or ""])
        if not tags:
            raise GroupError("Choose the tag whose VMs are backed up")
        value = tags[0]
    elif selection == "pool":
        if not value or not value.isdigit() or int(value) not in {p["id"] for p in permissions.list_pools()}:
            raise GroupError("Choose an existing pool")
    else:
        value = None
    excluded = sorted({str(v) for v in payload.get("exclues") or [] if v})
    if payload.get("frequence") not in FREQUENCIES:
        raise GroupError("Invalid frequency")
    if not TIME_RE.match(payload.get("heure") or ""):
        raise GroupError("Invalid time (expected HH:MM)")
    target = validate_target(payload.get("cible_dir")) or str(DEFAULT_BACKUP_DIR)
    policy = {}
    for key, low, high in (
        ("retention_count", 1, 365),
        ("garder_jours", 1, 366),
        ("garder_semaines", 1, 260),
        ("garder_mois", 1, 120),
    ):
        v = payload.get(key)
        if v is None and key != "retention_count":
            policy[key] = None
            continue
        if not isinstance(v, int) or not low <= v <= high:
            raise GroupError(f"{key} must be between {low} and {high}")
        policy[key] = v
    return {
        "nom": name,
        "selection": selection,
        "valeur": value,
        "exclues": excluded,
        "frequence": payload["frequence"],
        "heure": payload["heure"],
        "cible_dir": target,
        "actif": bool(payload.get("actif", True)),
        **policy,
    }


def _row(row):
    d = dict(row)
    d["exclues"] = json.loads(d["exclues"] or "[]")
    d["actif"] = bool(d["actif"])
    d["en_cours"] = d["id"] in _running
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
    "nom",
    "selection",
    "valeur",
    "exclues",
    "frequence",
    "heure",
    "cible_dir",
    "retention_count",
    "garder_jours",
    "garder_semaines",
    "garder_mois",
    "actif",
)


def save_job(payload, job_id=None):
    from app.core.backups import _next_run

    clean = validate(payload)
    next_run = _next_run(clean["frequence"], clean["heure"]).isoformat()
    if _store().group_name_taken(clean["nom"], job_id):
        raise GroupError(f"A backup job named '{clean['nom']}' already exists")
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
    if job["selection"] == "etiquette":
        tagged = {
            m["nom"] for m in object_meta.list_all("vm") if m["node"] in (None, "local") and job["valeur"] in m["tags"]
        }
        names = [n for n in names if n in tagged]
    elif job["selection"] == "pool":
        pool = next((p for p in permissions.list_pools() if str(p["id"]) == str(job["valeur"])), None)
        members = set(pool["vms"]) if pool else set()
        names = [n for n in names if n in members]
    excluded = set(job["exclues"])
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
                backup_id = run_backup(vm, job["cible_dir"], username=username)
            except vm_locks.VmBusy as e:
                results[vm] = f"skipped: {e}"
                continue
            except Exception as e:
                results[vm] = str(e) or type(e).__name__
                continue
            _store().set_group(backup_id, job["id"])
            own = _store().get_schedule(vm)
            if not own:
                _apply_retention(vm, policy)
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
            job["nom"],
            "echec" if failed else "succes",
            "; ".join(f"{vm}: {r}" for vm, r in failed.items())[:500] or f"{len(results)} VMs",
        )
        _store().record_group_run(
            job["id"],
            now.isoformat(),
            _next_run(job["frequence"], job["heure"], now).isoformat(),
            shared_next=row["prochaine_partagee"] if own else None,
        )
