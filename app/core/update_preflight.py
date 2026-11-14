"""Checks before an update (docs/design/updates-2.md, 2.2): what would make it fail half-way, or surprise someone.

Each check answers `ok`, `warning` (said; the administrator starts anyway, knowing it) or `blocking` (the update
does not start). The probes are small functions the tests replace; none of them changes anything.
"""

import fcntl
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

from app.core import paths

MIN_FREE_BYTES = int(1.5 * 1024**3)
DPKG_LOCKS = ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock")
# Operations an API restart would end, or that an update should not run under: what an administrator should know.
BUSY_TASKS = {
    "backup_vm", "restore_backup", "migrate_vm", "clone_vm", "move_disk", "create_vm", "export_vm",
    "deploy_template", "gallery_download", "import_backup_store", "drain_node", "create_k8s_cluster",
}  # fmt: skip
RECENT = timedelta(hours=12)


def _free(path):
    return shutil.disk_usage(path).free


def _dpkg_audit():
    """dpkg's own list of packages left half-installed (empty when all is well)."""
    try:
        r = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout or "").strip()


def _lock_held():
    """The path of a dpkg lock another program holds, or None."""
    for path in DPKG_LOCKS:
        try:
            with open(path, "a") as f:
                try:
                    fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.lockf(f, fcntl.LOCK_UN)
                except OSError:
                    return path
        except OSError:
            continue  # not there (not Debian) or not readable: nothing to say
    return None


def _busy_tasks():
    from app.repositories import registry

    since = (datetime.now(UTC) - RECENT).isoformat()
    return [(t, n) for t, n in registry.tasks().sync.running_since(since) if t in BUSY_TASKS]


def _in_cluster():
    from app.core import cluster_lead

    return cluster_lead.in_cluster()


def _reboot_pending():
    from app.core import host_system

    return host_system.REBOOT_REQUIRED.exists()


def _compare(a, b):
    """-1, 0 or 1 as package version a is older, equal or newer than b (dpkg's ordering)."""
    from app.routers.update import _newer

    if a == b:
        return 0
    return 1 if _newer(a, b) else -1


def _major(v):
    try:
        return int(str(v).split(".")[0])
    except ValueError:
        return None


def run(installed, candidate, running_for=None):
    """The checks for an update from `installed` to `candidate` (None: Debian only). `running_for`: who runs an update
    now, if anyone."""
    out = []

    def add(check_id, status, detail=None, **values):
        out.append({"id": check_id, "status": status, "detail": detail, **values})

    if running_for:
        add("concurrent", "blocking", f"An update started by {running_for} is running", who=running_for)
    else:
        add("concurrent", "ok")

    for check_id, path in (("space_root", "/"), ("space_backup", str(paths.UPDATE_BACKUPS.parent))):
        try:
            free = _free(path)
        except OSError:
            continue
        status = "ok" if free >= MIN_FREE_BYTES else "blocking"
        add(
            check_id,
            status,
            None if status == "ok" else f"{free // 1024**2} MiB free on {path}",
            free=free,
            needed=MIN_FREE_BYTES,
            path=path,
        )

    audit = _dpkg_audit()
    add("dpkg", "blocking" if audit else "ok", audit[:600] if audit else None)

    held = _lock_held()
    add("apt_lock", "blocking" if held else "ok", held)

    busy = _busy_tasks()
    add(
        "operations",
        "warning" if busy else "ok",
        ", ".join(f"{t} {n or ''}".strip() for t, n in busy[:10]) or None,
        count=len(busy),
    )

    if candidate and installed:
        order = _compare(candidate, installed)
        if order < 0:
            add("version", "blocking", f"{candidate} is older than {installed}")
        elif _major(candidate) != _major(installed):
            add("version", "warning", f"{installed} to {candidate}: another major version")
        else:
            add("version", "ok")

    add("cluster", "warning" if _in_cluster() else "ok")
    add("reboot_pending", "warning" if _reboot_pending() else "ok")
    return out


def blocking(checks):
    return [c for c in checks if c["status"] == "blocking"]
