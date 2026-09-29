"""File-level restore from a backup (app/core/file_restore.py). Administrators only, like a restore: the files of
any VM's backup are readable through it."""

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.core import file_restore
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(tags=["backups"])


def _fail(e):
    return HTTPException(status_code=422, detail=str(e))


@router.get("/file-restore/status")
def restore_status(user: dict = Depends(require_role("admin"))):
    return file_restore.status()


@router.post("/backups/{backup_id}/files")
def open_backup_files(backup_id: int, user: dict = Depends(require_role("admin"))):
    """Open the backup's disks for browsing (the first request of a session takes seconds: the reader starts)."""
    try:
        s = file_restore.open_session(backup_id, user["username"])
    except file_restore.RestoreError as e:
        log_action(user["username"], "browse_backup", str(backup_id), "echec", str(e))
        raise _fail(e) from e
    log_action(user["username"], "browse_backup", f"{s.vm_name} #{backup_id}", "succes")
    return {"session": s.id, "vm": s.vm_name, "filesystems": s.filesystems}


@router.get("/file-restore/{session_id}/ls")
def list_backup_dir(session_id: str, device: str, path: str = "/", user: dict = Depends(require_role("admin"))):
    try:
        s = file_restore.get_session(session_id, user["username"])
        return s.request({"op": "ls", "device": device, "path": path})
    except file_restore.RestoreError as e:
        raise _fail(e) from e


@router.get("/file-restore/{session_id}/download")
def download_from_backup(session_id: str, device: str, path: str, user: dict = Depends(require_role("admin"))):
    """A file as it is, a folder as a .tar.gz; the temporary copy is removed once sent."""
    try:
        s = file_restore.get_session(session_id, user["username"])
        dest, name = file_restore.export(s, device, path)
    except file_restore.RestoreError as e:
        log_action(user["username"], "restore_file", path, "echec", str(e))
        raise _fail(e) from e
    log_action(user["username"], "restore_file", f"{s.vm_name} #{s.backup_id}:{device}{path}", "succes")
    return FileResponse(
        dest, filename=name, media_type="application/octet-stream", background=BackgroundTask(os.unlink, dest)
    )


@router.delete("/file-restore/{session_id}")
def close_backup_files(session_id: str, user: dict = Depends(require_role("admin"))):
    try:
        file_restore.close_session(session_id, user["username"])
    except file_restore.RestoreError as e:
        raise _fail(e) from e
    return {"message": "Closed"}
