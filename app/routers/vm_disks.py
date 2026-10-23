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
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core import ova, paths
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.listing import listing
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.tasks import answer_later, create_task, finish_task

router = APIRouter(prefix="/vm-disks", tags=["vm-disks"])

IMPORTED_DISKS_DIR = paths.IMPORTED_DISKS
IMPORTED_DISKS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = (".qcow2", ".img", ".raw", ".vmdk", ".vdi", ".vhd", ".vhdx", ".ova")


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

    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        finish_task(task_id, "failed", "Extension non reconnue")
        raise HTTPException(
            status_code=422, detail=f"Unrecognized extension (expected: {', '.join(ALLOWED_EXTENSIONS)})"
        )

    dest = safe_child(IMPORTED_DISKS_DIR, filename)
    try:
        try:
            with open(dest, "wb") as out:
                shutil.copyfileobj(file.file, out)
        finally:
            await file.close()
    except OSError as e:
        # Safety net: without it, a failing write (disk full, permissions...) would
        # leave the task stuck in "running" forever (same trap as in isos.py).
        msg = describe_exception(e)
        finish_task(task_id, "failed", msg)
        raise HTTPException(status_code=500, detail=f"Failed to write the disk: {msg}") from e

    finish_task(task_id, "succeeded")
    log_action(user["username"], "upload_vm_disk", filename, "success")
    return {"name": filename, "size_mb": round(dest.stat().st_size / (1024 * 1024), 1)}


@router.delete("/{filename}")
def delete_vm_disk(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = safe_child(IMPORTED_DISKS_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Disk '{filename}' not found")
    path.unlink()
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
    filename, path = _ova_path(filename)
    task_id = create_task("extract_ova", filename, username=user["username"])
    result = {}

    def work():
        try:
            result["disks"] = ova.extract_disks(path, IMPORTED_DISKS_DIR, _disk_stem(filename))
        except ova.OvaError as e:
            log_action(user["username"], "extract_ova", filename, "failed", str(e), task_id=task_id)
            raise HTTPException(status_code=422, detail=str(e)) from None
        except OSError as e:
            msg = describe_exception(e)
            log_action(user["username"], "extract_ova", filename, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"The disks could not be extracted: {msg}") from e
        log_action(user["username"], "extract_ova", filename, "success", ", ".join(result["disks"]), task_id=task_id)

    if not wait:
        return answer_later(task_id, work, name=filename)
    # Every way out of work() closes the task through log_action.
    work()
    return {"name": filename, "disks": result["disks"]}
