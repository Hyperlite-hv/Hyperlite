"""File-level restore from a backup (app/core/file_restore.py). Administrators only, like a restore: the files of
any VM's backup are readable through it."""

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.core import cluster_view, file_restore
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(tags=["backups"])


def _attachment(name):
    """The download's Content-Disposition, as FileResponse writes it: a guest's file name may be anything."""
    quoted = quote(name)
    return f"attachment; filename*=utf-8''{quoted}" if quoted != name else f'attachment; filename="{name}"'


def _fail(e):
    return HTTPException(status_code=422, detail=str(e))


@router.get("/file-restore/status")
def restore_status(user: dict = Depends(require_role("admin"))):
    return file_restore.status()


@router.post("/backups/{backup_id}/files")
def open_backup_files(backup_id: int, on: str | None = None, user: dict = Depends(require_role("admin"))):
    """Open the backup's disks for browsing (the first request of a session takes seconds: the reader starts).
    on: the node that holds the backup in a cluster. Its files are browsed from that node's own dashboard: the
    downloads are files, which are not sent through another node."""
    try:
        holder = cluster_view.peer(on)
    except LookupError:
        holder = {"name": on}
    if holder is not None:
        raise HTTPException(status_code=409, detail=f"Open the dashboard of {holder['name']} to browse this backup")
    try:
        s = file_restore.open_session(backup_id, user["username"])
    except file_restore.RestoreError as e:
        log_action(user["username"], "browse_backup", str(backup_id), "failed", str(e))
        raise _fail(e) from e
    log_action(user["username"], "browse_backup", f"{s.vm_name} #{backup_id}", "success")
    return {"session": s.id, "vm": s.vm_name, "filesystems": s.filesystems}


@router.get("/file-restore/{session_id}/ls")
def list_backup_dir(session_id: str, device: str, path: str = "/", user: dict = Depends(require_role("admin"))):
    try:
        file_restore.get_session(session_id, user["username"])
        return file_restore.list_dir(session_id, user["username"], device, path)
    except file_restore.RestoreError as e:
        raise _fail(e) from e


@router.get("/file-restore/{session_id}/download")
def download_from_backup(session_id: str, device: str, path: str, user: dict = Depends(require_role("admin"))):
    """A file as it is, a folder as a .tar.gz; the temporary copy is removed once sent."""
    try:
        s = file_restore.get_session(session_id, user["username"])
        fd, name = file_restore.export(session_id, user["username"], device, path)
    except file_restore.RestoreError as e:
        log_action(user["username"], "restore_file", path, "failed", str(e))
        raise _fail(e) from e
    log_action(user["username"], "restore_file", f"{s.vm_name} #{s.backup_id}:{device}{path}", "success")
    # hyperlite-daemon hands the copy over as an open file, already unlinked: it goes once read and closed.
    copy = os.fdopen(fd, "rb")
    return StreamingResponse(
        iter(lambda: copy.read(1 << 20), b""),
        media_type="application/octet-stream",
        headers={"Content-Disposition": _attachment(name)},
        background=BackgroundTask(copy.close),
    )


@router.delete("/file-restore/{session_id}")
def close_backup_files(session_id: str, user: dict = Depends(require_role("admin"))):
    try:
        file_restore.close_session(session_id, user["username"])
    except file_restore.RestoreError as e:
        raise _fail(e) from e
    return {"message": "Closed"}
