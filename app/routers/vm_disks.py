"""Upload/gestion des fichiers disque destines a l'import de VM (chantier
23, 2026-09-13) -- pendant du chantier des ISO (isos.py) mais pour un
disque deja installe (qcow2/raw/vmdk/vdi/vhd/...) plutot qu'un media
d'installation. Meme mecanisme d'upload simple (une seule requete, streame
directement sur disque via shutil.copyfileobj, pas de chunking/reprise) --
deja la limite connue de l'upload ISO existant, un disque de VM peut etre
tout aussi volumineux mais l'app n'a aujourd'hui aucun mecanisme resumable
nulle part, pas la peine d'en inventer un pour ce seul endpoint. qemu-img
(utilise a la creation de la VM, pas ici, voir vm_builder.create_disk_from_
import) detecte le format source tout seul, aucune conversion necessaire a
l'upload lui-meme."""
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
        raise HTTPException(status_code=422, detail=f"Extension non reconnue (attendu : {', '.join(ALLOWED_EXTENSIONS)})")

    dest = IMPORTED_DISKS_DIR / filename
    try:
        try:
            with open(dest, "wb") as out:
                shutil.copyfileobj(file.file, out)
        finally:
            await file.close()
    except OSError as e:
        # Sans ce filet, une ecriture qui echoue (disque plein, permissions...)
        # laisserait la tache bloquee "en_cours" indefiniment (voir isos.py,
        # meme piege deja corrige la-bas).
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        raise HTTPException(status_code=500, detail=f"Échec de l'écriture du disque : {msg}")

    finish_task(task_id, "termine")
    log_action(user["username"], "upload_vm_disk", filename, "succes")
    return {"nom": filename, "taille_mo": round(dest.stat().st_size / (1024 * 1024), 1)}


@router.delete("/{filename}")
def delete_vm_disk(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = IMPORTED_DISKS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Disque '{filename}' introuvable")
    path.unlink()
    log_action(user["username"], "delete_vm_disk", filename, "succes")
    return {"nom": filename, "supprime": True}
