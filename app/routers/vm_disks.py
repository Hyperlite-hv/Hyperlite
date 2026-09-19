"""Upload and management of disk files intended for VM import. The
counterpart of the ISO endpoints (isos.py) but for an already installed disk
(qcow2/raw/vmdk/vdi/vhd/...) rather than an installation medium. It uses the
same simple upload mechanism (a single request streamed straight to disk via
shutil.copyfileobj, no chunking or resume): the same known limit as the ISO
upload, and since the application has no resumable mechanism anywhere it is
not worth inventing one for this endpoint alone. qemu-img (used when the VM is
created, not here, see vm_builder.create_disk_from_import) detects the source
format by itself, so no conversion is needed at upload time."""

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task

router = APIRouter(prefix="/vm-disks", tags=["vm-disks"])

IMPORTED_DISKS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "imported-disks"
IMPORTED_DISKS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = (".qcow2", ".img", ".raw", ".vmdk", ".vdi", ".vhd", ".vhdx")


@router.get("")
def list_vm_disks(user: dict = Depends(get_current_user)):
    result = []
    for p in sorted(IMPORTED_DISKS_DIR.iterdir()):
        if p.is_file():
            result.append({"nom": p.name, "taille_mo": round(p.stat().st_size / (1024 * 1024), 1)})
    return result


@router.post("", status_code=201)
async def upload_vm_disk(file: UploadFile = File(...), user: dict = Depends(require_role("admin"))):
    filename = Path(file.filename or "").name
    task_id = create_task("upload_vm_disk", filename, username=user["username"])

    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        finish_task(task_id, "echec", "Extension non reconnue")
        raise HTTPException(
            status_code=422, detail=f"Unrecognized extension (expected: {', '.join(ALLOWED_EXTENSIONS)})"
        )

    dest = IMPORTED_DISKS_DIR / filename
    try:
        try:
            with open(dest, "wb") as out:
                shutil.copyfileobj(file.file, out)
        finally:
            await file.close()
    except OSError as e:
        # Safety net: without it, a failing write (disk full, permissions...) would
        # leave the task stuck in "en_cours" forever (same trap as in isos.py).
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        raise HTTPException(status_code=500, detail=f"Failed to write the disk: {msg}") from e

    finish_task(task_id, "termine")
    log_action(user["username"], "upload_vm_disk", filename, "succes")
    return {"nom": filename, "taille_mo": round(dest.stat().st_size / (1024 * 1024), 1)}


@router.delete("/{filename}")
def delete_vm_disk(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = IMPORTED_DISKS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Disk '{filename}' not found")
    path.unlink()
    log_action(user["username"], "delete_vm_disk", filename, "succes")
    return {"nom": filename, "supprime": True}
