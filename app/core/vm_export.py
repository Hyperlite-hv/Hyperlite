"""VM export/import through a disk file. A backup (app/core/backups.py) stays
on this host and is meant for local restoration; an export instead produces a
downloadable qcow2 file meant to LEAVE the host (migration to another server,
external archive, sharing). Symmetrically, an imported disk (see
app/routers/vm_disks.py) is a file that came from elsewhere and is used to
create a new VM directly, without installing it from an ISO.

It reuses the building blocks of the backup module (domain_disk_paths,
qemu_img_convert_with_progress, backup_cold/backup_hot with their tested
hot/cold mechanism) instead of duplicating the logic. It is deliberately
restricted to the system disk (index 0, "sda"): backing up a whole multi-disk
VM is the job of the backup feature. On import, no attempt is made to inject
cloud-init or an SSH key into the imported disk (unlike VMs created from
scratch): nothing is known about its content (OS, filesystem, existing
accounts), so it boots as is and is accessed with the credentials already on
it."""

import re
import shutil
from datetime import UTC, datetime

import libvirt

from app.core import paths
from app.core.audit import log_action
from app.core.backups import backup_cold, backup_hot, block_disk_error, domain_disk_paths
from app.core.error_messages import describe_exception
from app.core.safe_paths import safe_child
from app.core.tasks import create_task, finish_task, update_task_progress

EXPORTS_DIR = paths.EXPORTS
# What an export is named (`<vm>--<time>.qcow2`, export_copy): the only files vm.export_delete removes.
EXPORT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}--\d{8}T\d{6}Z\.qcow2")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def list_exports():
    result = []
    for p in sorted(EXPORTS_DIR.glob("*.qcow2"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        result.append(
            {
                "name": p.name,
                "size_bytes": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            }
        )
    return result


def run_export(vm_name, username="system"):
    """Synchronous: called from a thread by the endpoint (see app/routers/vm_export.py), same pattern as
    run_backup(). The task and the audit are the API's; hyperlite-daemon copies the disk (vm.export)."""
    from app.core import privileged
    from app.core.backups import JOB_TIMEOUT_S
    from app.daemon.registry import DaemonError

    task_id = create_task("export_vm", vm_name, node=None, username=username)
    try:
        try:
            done = privileged.run("vm.export", user=username, timeout=JOB_TIMEOUT_S, vm=vm_name, task_id=task_id)
        except DaemonError as e:
            raise RuntimeError(e.detail) from None
        finish_task(task_id, "succeeded")
        log_action(username, "export_vm", vm_name, "success", f"-> {done['name']}")
        return {**done, "task_id": task_id}
    except Exception as e:
        finish_task(task_id, "failed", str(e))
        log_action(username, "export_vm", vm_name, "failed", str(e))
        raise


def export_copy(vm_name, task_id):
    """hyperlite-daemon's part of an export (vm.export): the system disk copied cold or live into the exports
    directory. {"name", "size_bytes"}."""
    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        error = block_disk_error(domain, "An export")
        if error:
            raise RuntimeError(error)
        all_disks = domain_disk_paths(domain)
        if not all_disks:
            raise RuntimeError("No disk found on this VM")
        disk0 = all_disks[:1]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        work_dir = safe_child(EXPORTS_DIR, f".tmp-{vm_name}-{stamp}")
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            if domain.isActive():
                dest_paths = backup_hot(conn, domain, vm_name, work_dir, task_id, disks=disk0)
            else:
                dest_paths = backup_cold(domain, vm_name, work_dir, task_id, disks=disk0)
            final = safe_child(EXPORTS_DIR, f"{vm_name}--{stamp}.qcow2")
            shutil.move(str(dest_paths[0]), str(final))
            update_task_progress(task_id, 100)
            return {"name": final.name, "size_bytes": final.stat().st_size}
        except libvirt.libvirtError as e:
            raise RuntimeError(describe_exception(e)) from e
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


def delete_export(name):
    """hyperlite-daemon's part of an export's deletion (vm.export_delete): a file of the exports directory only.
    False when there is none by that name."""
    if not EXPORT_NAME.fullmatch(name or ""):
        raise ValueError(f"Not an export: {name}")
    path = safe_child(EXPORTS_DIR, name)
    if not path.is_file() or path.is_symlink():
        return False
    path.unlink()
    return True
