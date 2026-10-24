import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core import cluster_view, disk_library, privileged
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.listing import listing
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

# ?node= on every route: the member that holds the object does the work (app/core/cluster_view.py, on_node).
router = APIRouter(prefix="/isos", tags=["isos"], dependencies=[Depends(cluster_view.on_node)])

ISO_NAME_RE = disk_library.ISO_NAME_RE

# This node's library, written by hyperlite-daemon (app/core/disk_library.py); the VM routes read it under this name.
ISOS_DIR = disk_library.ISOS
ISOS_DIR.mkdir(parents=True, exist_ok=True)


def _iso_in_use(conn, iso_path: str) -> bool:
    return disk_library.in_cdrom(conn, iso_path)


@router.get("")
def list_isos(user: dict = Depends(get_current_user)):
    from app.core.iso_share import local_isos

    return listing(local_isos())


@router.get("/cluster")
def list_cluster_isos(user: dict = Depends(get_current_user)):
    """Every node's library, with the nodes that could not be read."""
    from app.core.iso_share import cluster_isos

    return cluster_isos()


class IsoCopy(BaseModel):
    name: str
    source: str = "local"
    targets: list[str]


@router.post("/copy", status_code=202)
def copy_iso(payload: IsoCopy, user: dict = Depends(require_role("admin"))):
    """Copy an image from one node's library to others, one background task per target node."""
    from app.core.iso_share import start_copy

    try:
        tasks = start_copy(payload.name, payload.source, payload.targets, user["username"])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"name": payload.name, "tasks": tasks}


def _write_upload(source, partial):
    with open(partial, "wb") as out:
        shutil.copyfileobj(source, out, 8 * 1024 * 1024)


async def store_upload(file, library, name, user, task_id, failed, replace=False):
    """An upload written into the spool, then moved into its library by hyperlite-daemon (app/core/disk_library.py,
    as pveproxy writes an upload and pvedaemon stores it). Returns its size in bytes; any failure ends the task with
    its reason and leaves nothing in the spool."""
    spool_name, partial = disk_library.spool_file()
    try:
        try:
            # The copy (gigabytes) runs in a worker thread: in this coroutine it held up every other request meanwhile.
            await run_in_threadpool(_write_upload, file.file, partial)
        finally:
            await file.close()
        return (
            await run_in_threadpool(
                privileged.run,
                "disk.library_store",
                user=user["username"],
                library=library,
                spool_name=spool_name,
                name=name,
                replace=replace,
            )
        )["size"]
    except DaemonError as e:
        finish_task(task_id, "failed", e.detail)
        log_action(user["username"], f"upload_{'iso' if library == 'iso' else 'vm_disk'}", name, "failed", e.detail)
        raise privileged.http_error(e, refused=409, failed=failed) from None
    except BaseException as e:
        # Safety net: without it, a failing write (disk full, permissions...) would
        # leave the task stuck in "running" forever in the task list, never
        # "succeeded" and never "failed".
        msg = describe_exception(e) if isinstance(e, OSError) else "upload interrupted"
        finish_task(task_id, "failed", msg)
        if isinstance(e, OSError):
            raise HTTPException(status_code=500, detail=failed.format(msg)) from e
        raise
    finally:
        partial.unlink(missing_ok=True)


@router.post("", status_code=201)
async def upload_iso(file: UploadFile = File(...), user: dict = Depends(require_role("admin"))):
    filename = Path(file.filename or "").name
    task_id = create_task("upload_iso", filename, username=user["username"])

    if not filename.lower().endswith(".iso"):
        finish_task(task_id, "failed", "The file must have the .iso extension")
        raise HTTPException(status_code=422, detail="The file must have the .iso extension")
    # Not the VM name rule: distribution ISOs are named with dots and underscores
    # (debian-13.1.0-amd64-netinst.iso, ubuntu-24.04.3-live-server-amd64.iso).
    if not ISO_NAME_RE.fullmatch(filename[:-4]):
        msg = "Invalid ISO file name (letters, digits, dots, dashes, underscores and +, starting with a letter or a digit)"
        finish_task(task_id, "failed", msg)
        raise HTTPException(status_code=422, detail=msg)

    dest = safe_child(disk_library.ISOS, filename)
    # An upload under an existing name replaced it silently, even in a VM's CD-ROM drive: rename or delete it first.
    if dest.exists():
        await file.close()
        msg = f"An ISO named '{filename}' already exists: rename or delete it first"
        finish_task(task_id, "failed", msg)
        raise HTTPException(status_code=409, detail=msg)
    # Written into the spool, then moved into the library whole: an interrupted upload never leaves a truncated ISO
    # there, and another upload of the same name that finished meanwhile is not replaced (409).
    size = await store_upload(file, "iso", filename, user, task_id, "Failed to write the ISO: {}")
    log_action(user["username"], "upload_iso", filename, "success", task_id=task_id)
    return {"name": filename, "size_mb": round(size / (1024 * 1024), 1)}


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
    privileged.ask("disk.library_rename", user, "rename_iso", old, refused=409, name=old, new_name=new)
    log_action(user["username"], "rename_iso", old, "success", f"-> {new}")
    return {"name": new, "old": old}


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
    path = safe_child(disk_library.ISOS, filename)
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
        privileged.ask("disk.library_delete", user, "delete_iso", filename, library="iso", name=filename)
    else:
        try:
            delete_remote(remote, filename)
        except (RuntimeError, OSError) as e:
            raise HTTPException(status_code=502, detail=f"Deletion on {node} failed: {e}") from e
    target = filename if remote is None else f"{filename} ({node})"
    log_action(user["username"], "delete_iso", target, "success")
    return {"name": filename, "node": node, "deleted": True}
