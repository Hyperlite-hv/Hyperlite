import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.security import get_current_user
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.vm_builder import validate_name

router = APIRouter(prefix="/isos", tags=["isos"])

ISOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "isos"
ISOS_DIR.mkdir(parents=True, exist_ok=True)


def _iso_in_use(conn, iso_path: str) -> bool:
    for domain in conn.listAllDomains():
        try:
            root = ET.fromstring(domain.XMLDesc(0))
        except libvirt.libvirtError:
            continue
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") != "cdrom":
                continue
            source = disk.find("source")
            if source is not None and source.get("file") == iso_path:
                return True
    return False


@router.get("")
def list_isos(user: dict = Depends(get_current_user)):
    result = []
    for p in sorted(ISOS_DIR.glob("*.iso")):
        result.append({"nom": p.name, "taille_mo": round(p.stat().st_size / (1024 * 1024), 1)})
    return result


@router.post("", status_code=201)
async def upload_iso(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".iso"):
        raise HTTPException(status_code=422, detail="Le fichier doit avoir l'extension .iso")
    base_name = filename[:-4]
    try:
        validate_name(base_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    dest = ISOS_DIR / filename
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        await file.close()

    log_action(user["username"], "upload_iso", filename, "succes")
    return {"nom": filename, "taille_mo": round(dest.stat().st_size / (1024 * 1024), 1)}


@router.delete("/{filename}")
def delete_iso(filename: str, confirm: bool = False, user: dict = Depends(get_current_user)):
    filename = Path(filename).name
    if not filename.lower().endswith(".iso"):
        raise HTTPException(status_code=422, detail="Nom de fichier invalide")
    path = ISOS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"ISO '{filename}' introuvable")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation requise (?confirm=true)")

    conn = open_conn()
    try:
        if _iso_in_use(conn, str(path)):
            raise HTTPException(status_code=409, detail="ISO utilisee par une VM, ejectez-la d'abord")
    finally:
        conn.close()

    path.unlink()
    log_action(user["username"], "delete_iso", filename, "succes")
    return {"nom": filename, "supprime": True}
