"""VM export/import through a disk file. A backup (app/core/backups.py) stays
on this host and is meant for local restoration; an export instead produces a
downloadable qcow2 file meant to LEAVE the host (migration to another server,
external archive, sharing). Symmetrically, an imported disk (see
app/routers/vm_disks.py) is a file that came from elsewhere and is used to
create a new VM directly, without going through ISO + kickstart.

It reuses the building blocks of the backup module (domain_disk_paths,
qemu_img_convert_with_progress, backup_cold/backup_hot with their tested
hot/cold mechanism) instead of duplicating the logic. It is deliberately
restricted to the system disk (index 0, "sda"): backing up a whole multi-disk
VM is the job of the backup feature. On import, no attempt is made to inject
cloud-init or an SSH key into the imported disk (unlike VMs created from
scratch): nothing is known about its content (OS, filesystem, existing
accounts), so it boots as is and is accessed with the credentials already on
it."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.backups import backup_cold, backup_hot, domain_disk_paths
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.safe_paths import safe_child
from app.core.tasks import create_task, finish_task, update_task_progress

EXPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vm-exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def list_exports():
    result = []
    for p in sorted(EXPORTS_DIR.glob("*.qcow2"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        result.append(
            {
                "nom": p.name,
                "taille_octets": st.st_size,
                "modifie_le": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            }
        )
    return result


def run_export(vm_name, username="system"):
    """Synchronous: called from a thread by the endpoint (see
    app/routers/vm_export.py), same pattern as run_backup()."""
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None

        all_disks = domain_disk_paths(domain)
        if not all_disks:
            raise RuntimeError("No disk found on this VM")
        disk0 = all_disks[:1]

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        task_id = create_task("export_vm", vm_name, node=conn.getHostname(), username=username)
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
            finish_task(task_id, "termine")
            log_action(username, "export_vm", vm_name, "succes", f"-> {final.name}")
            return {"nom": final.name, "taille_octets": final.stat().st_size, "task_id": task_id}
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            finish_task(task_id, "echec", msg)
            log_action(username, "export_vm", vm_name, "echec", msg)
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    finally:
        conn.close()
