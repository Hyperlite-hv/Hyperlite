"""Upload and management of disk files intended for VM import. The
counterpart of the ISO endpoints (isos.py) purpose for an already installed disk
(qcow2/raw/vmdk/vdi/vhd/...) rather than an installation medium. It uses the
same simple upload mechanism (a single request streamed straight to disk via
shutil.copyfileobj, no chunking or resume): the same known limit as the ISO
upload, and since the application has no resumable mechanism anywhere it is
not worth inventing one for this endpoint alone. qemu-img (used when the VM is
created, not here, see vm_builder.create_disk_from_import) detects the source
format by itself, so no conversion is needed at upload time.

An OVA exported from VMware is uploaded the same way: GET .../ovf maps its VM to Hyperlite's settings, and
POST .../extract copies its disks out as imported disks, from which the VM is created (app/core/ova.py)."""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core import disk_library, ova, privileged
from app.core.audit import log_action
from app.core.listing import listing
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.tasks import answer_later, create_task, finish_task
from app.daemon.registry import DaemonError
from app.routers.isos import store_upload

router = APIRouter(prefix="/vm-disks", tags=["vm-disks"])

# This node's disks to import, written by hyperlite-daemon (app/core/disk_library.py); VM creation reads them here.
IMPORTED_DISKS_DIR = disk_library.IMPORTS
IMPORTED_DISKS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = disk_library.IMPORT_EXTENSIONS

# An OVA's disks are copied out in one call that waits: hours for the largest, as a backup's copy.
EXTRACT_TIMEOUT_S = 6 * 3600


@router.get("")
def list_vm_disks(user: dict = Depends(get_current_user)):
    result = []
    for p in sorted(IMPORTED_DISKS_DIR.iterdir()):
        if p.is_file():
            result.append({"name": p.name, "size_mb": round(p.stat().st_size / (1024 * 1024), 1)})
    return listing(result)


@router.post("", status_code=201)
async def upload_vm_disk(file: UploadFile = File(...), user: dict = Depends(require_role("admin"))):
    filename = Path(file.filename or "").name
    task_id = create_task("upload_vm_disk", filename, username=user["username"])

    try:
        disk_library.check_name("import", filename)
    except disk_library.LibraryError as e:
        finish_task(task_id, "failed", e.message)
        raise HTTPException(status_code=422, detail=e.message) from None

    # A disk of the same name is replaced, as an upload always did: it is a copy's source, never a VM's own disk.
    size = await store_upload(file, "import", filename, user, task_id, "Failed to write the disk: {}", replace=True)
    finish_task(task_id, "succeeded")
    log_action(user["username"], "upload_vm_disk", filename, "success")
    return {"name": filename, "size_mb": round(size / (1024 * 1024), 1)}


@router.delete("/{filename}")
def delete_vm_disk(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = safe_child(IMPORTED_DISKS_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Disk '{filename}' not found")
    privileged.ask("disk.library_delete", user, "delete_vm_disk", filename, library="import", name=filename)
    log_action(user["username"], "delete_vm_disk", filename, "success")
    return {"name": filename, "deleted": True}


def _ova_path(filename):
    filename = Path(filename).name
    if not filename.lower().endswith(".ova"):
        raise HTTPException(status_code=422, detail="Not an OVA file")
    path = safe_child(IMPORTED_DISKS_DIR, filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")
    return filename, path


def _disk_stem(ova_filename):
    """The name the OVA's disks take among the imported disks: the OVA's, with what a file name should not hold."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", Path(ova_filename).stem)[:100] or "ova"


@router.get("/{filename}/ovf")
def read_ova(filename: str, user: dict = Depends(get_current_user)):
    """The VM an OVA holds, mapped to Hyperlite's settings, and the names its disks take once extracted."""
    filename, path = _ova_path(filename)
    try:
        info = ova.read(path)
    except ova.OvaError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    stem = _disk_stem(filename)
    for n, disk in enumerate(info["disks"], start=1):
        disk["import_disk"] = f"{stem}-disk{n}{Path(disk['file']).suffix.lower()}"
    return info


@router.post("/{filename}/extract")
def extract_ova(filename: str, wait: bool = True, user: dict = Depends(require_role("admin"))):
    """Copy the OVA's disks out as imported disks (checked against its manifest). wait=false: a task, 202."""
    filename, _ = _ova_path(filename)  # an OVA of the library: 422 or 404 before any task
    task_id = create_task("extract_ova", filename, username=user["username"])
    result = {}

    def work():
        # Copied out by hyperlite-daemon, which writes the imported disks (app/daemon/ops/disk.py).
        try:
            result["disks"] = privileged.run(
                "disk.ova_extract",
                user=user["username"],
                timeout=EXTRACT_TIMEOUT_S,
                ova=filename,
                stem=_disk_stem(filename),
            )["disks"]
        except DaemonError as e:
            log_action(user["username"], "extract_ova", filename, "failed", e.detail, task_id=task_id)
            raise privileged.http_error(e) from None
        log_action(user["username"], "extract_ova", filename, "success", ", ".join(result["disks"]), task_id=task_id)

    if not wait:
        return answer_later(task_id, work, name=filename)
    # Every way out of work() closes the task through log_action.
    work()
    return {"name": filename, "disks": result["disks"]}
