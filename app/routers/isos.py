import logging
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/isos", tags=["isos"])

ISO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}")

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
    from app.core.iso_share import local_isos

    return local_isos()


@router.get("/cluster")
def list_cluster_isos(user: dict = Depends(get_current_user)):
    """Every node's library, with the nodes that could not be read."""
    from app.core.iso_share import cluster_isos

    return cluster_isos()


class IsoCopy(BaseModel):
    nom: str
    source: str = "local"
    cibles: list[str]


@router.post("/copy", status_code=202)
def copy_iso(payload: IsoCopy, user: dict = Depends(require_role("admin"))):
    """Copy an image from one node's library to others, one background task per target node."""
    from app.core.iso_share import start_copy

    try:
        tasks = start_copy(payload.nom, payload.source, payload.cibles, user["username"])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"nom": payload.nom, "taches": tasks}


def _refresh_iso_pool(path):
    """The ISO was written directly, not through libvirt: refresh the pool that holds it (if any) so that it is
    listed right away. Best-effort, the upload itself succeeded."""
    try:
        conn = open_conn()
    except (libvirt.libvirtError, HTTPException):
        logger.warning("libvirt unreachable, the pool holding %s was not refreshed", path, exc_info=True)
        return
    try:
        refresh_pools_for_paths(conn, [path])
    finally:
        conn.close()


def _write_upload(source, partial, dest):
    with open(partial, "wb") as out:
        shutil.copyfileobj(source, out, 8 * 1024 * 1024)
    if dest.exists():  # another upload of the same name finished meanwhile
        raise FileExistsError(f"An ISO named '{dest.name}' already exists")
    partial.rename(dest)


@router.post("", status_code=201)
async def upload_iso(file: UploadFile = File(...), user: dict = Depends(require_role("admin"))):
    filename = Path(file.filename or "").name
    task_id = create_task("upload_iso", filename, username=user["username"])

    if not filename.lower().endswith(".iso"):
        finish_task(task_id, "echec", "The file must have the .iso extension")
        raise HTTPException(status_code=422, detail="The file must have the .iso extension")
    # Not the VM name rule: distribution ISOs are named with dots and underscores
    # (debian-13.1.0-amd64-netinst.iso, ubuntu-24.04.3-live-server-amd64.iso).
    if not ISO_NAME_RE.fullmatch(filename[:-4]):
        msg = "Invalid ISO file name (letters, digits, dots, dashes, underscores and +, starting with a letter or a digit)"
        finish_task(task_id, "echec", msg)
        raise HTTPException(status_code=422, detail=msg)

    dest = safe_child(ISOS_DIR, filename)
    # An upload under an existing name replaced it silently, even in a VM's CD-ROM drive: rename or delete it first.
    if dest.exists():
        await file.close()
        msg = f"An ISO named '{filename}' already exists: rename or delete it first"
        finish_task(task_id, "echec", msg)
        raise HTTPException(status_code=409, detail=msg)
    # Written under a temporary name, then renamed: an interrupted upload left a truncated ISO in the library. The
    # copy (gigabytes) runs in a worker thread: in this coroutine it held up every other request meanwhile.
    partial = dest.with_name(f".{filename}.part")
    try:
        try:
            await run_in_threadpool(_write_upload, file.file, partial, dest)
        finally:
            await file.close()
    except BaseException as e:
        partial.unlink(missing_ok=True)
        # Safety net: without it, a failing write (disk full, permissions...) would
        # leave the task stuck in "en_cours" forever in the task list, never
        # "termine" and never "echec".
        msg = describe_exception(e) if isinstance(e, OSError) else "upload interrupted"
        finish_task(task_id, "echec", msg)
        if isinstance(e, FileExistsError):
            raise HTTPException(status_code=409, detail=str(e)) from e
        if isinstance(e, OSError):
            raise HTTPException(status_code=500, detail=f"Failed to write the ISO: {msg}") from e
        raise

    _refresh_iso_pool(dest)
    log_action(user["username"], "upload_iso", filename, "succes", task_id=task_id)
    return {"nom": filename, "taille_mo": round(dest.stat().st_size / (1024 * 1024), 1)}


class IsoRename(BaseModel):
    new_name: str


@router.post("/{filename}/rename")
def rename_iso(filename: str, payload: IsoRename, user: dict = Depends(require_role("admin"))):
    """An ISO of this host's library. Refused while a VM has it in a CD-ROM drive: its definition names the file."""
    from app.core.iso_share import isos_dir

    old = Path(filename).name
    new = Path(payload.new_name.strip()).name
    if not new.lower().endswith(".iso"):
        new += ".iso"
    if not ISO_NAME_RE.fullmatch(new[:-4]):
        raise HTTPException(
            status_code=422,
            detail="Invalid ISO file name (letters, digits, dots, dashes, underscores and +, starting with a letter or a digit)",
        )
    if new == old:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    source, target = safe_child(isos_dir(), old), safe_child(isos_dir(), new)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"ISO '{old}' not found")
    if target.exists():
        raise HTTPException(status_code=409, detail=f"An ISO named '{new}' already exists")
    conn = open_conn()
    try:
        if _iso_in_use(conn, str(source)):
            raise HTTPException(
                status_code=409, detail="A VM has this ISO in its CD-ROM drive: eject it first (or rename after)"
            )
    finally:
        conn.close()
    try:
        source.rename(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Rename failed: {describe_exception(e)}") from e
    _refresh_iso_pool(target)
    log_action(user["username"], "rename_iso", old, "succes", f"-> {new}")
    return {"nom": new, "ancien": old}


@router.delete("/{filename}")
def delete_iso(filename: str, confirm: bool = False, node: str = "local", user: dict = Depends(require_role("admin"))):
    from app.core.iso_share import delete_remote, iso_size, resolve_node, valid_iso_name

    filename = Path(filename).name
    if not filename.lower().endswith(".iso"):
        raise HTTPException(status_code=422, detail="Invalid file name")
    try:
        remote = resolve_node(node)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if remote is not None and not valid_iso_name(filename):
        raise HTTPException(status_code=422, detail="Invalid file name")
    path = safe_child(ISOS_DIR, filename)
    exists = iso_size(remote, filename) is not None if remote is not None else path.exists()
    if not exists:
        raise HTTPException(status_code=404, detail=f"ISO '{filename}' not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required (?confirm=true)")

    # Every node keeps its library at the same path, so the in-use check reads that node's own VMs.
    conn = open_conn(None if remote is None else node)
    try:
        if _iso_in_use(conn, str(path)):
            raise HTTPException(status_code=409, detail="ISO in use by a VM, eject it first")
    finally:
        conn.close()

    if remote is None:
        path.unlink()
    else:
        try:
            delete_remote(remote, filename)
        except (RuntimeError, OSError) as e:
            raise HTTPException(status_code=502, detail=f"Deletion on {node} failed: {e}") from e
    target = filename if remote is None else f"{filename} ({node})"
    log_action(user["username"], "delete_iso", target, "succes")
    return {"nom": filename, "node": node, "supprime": True}
