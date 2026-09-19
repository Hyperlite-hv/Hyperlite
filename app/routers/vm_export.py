"""VM export endpoints (export a disk file). The actual logic is in
app/core/vm_export.py (which reuses app/core/backups.py); this file validates
input, checks permissions and orchestrates the background task, following the
same pattern as app/routers/backups.py.

Download uses a single-use ticket (the same mechanism as the web SSH
terminals, app/routers/vms.py::create_terminal_ticket) rather than a plain
GET protected by a JWT: the application authenticates with a Bearer token (see
app/core/security.py), which cannot be attached to a regular <a href> or
browser download link. A short-lived ticket, obtained through a prior
authenticated call, is encoded in the URL itself."""

import logging
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.audit import log_action
from app.core.safe_paths import safe_child
from app.core.security import require_role, require_vm_privilege
from app.core.vm_export import EXPORTS_DIR, list_exports, run_export

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vm-export"])

DOWNLOAD_TICKETS = {}
DOWNLOAD_TICKET_TTL = 60


@router.get("/vm-exports")
def list_vm_exports(user: dict = Depends(require_role("admin"))):
    return list_exports()


@router.post("/vms/{name}/export", status_code=202)
def export_vm(name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    # Reuses the vm.snapshot privilege (protecting a VM's state, same spirit as
    # backups, see backups.py) rather than a new dedicated privilege.
    def job():
        try:
            run_export(name, username=user["username"])
        except Exception:
            logger.debug(
                "Ignored exception in job()", exc_info=True
            )  # already logged and tracked in run_export (task + audit_log)

    threading.Thread(target=job, daemon=True).start()
    log_action(user["username"], "export_vm_requested", name, "succes")
    return {"message": f"Export of '{name}' started in the background"}


@router.delete("/vm-exports/{filename}")
def delete_vm_export(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = safe_child(EXPORTS_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    path.unlink()
    log_action(user["username"], "delete_vm_export", filename, "succes")
    return {"nom": filename, "supprime": True}


@router.post("/vm-exports/{filename}/download-ticket")
def create_download_ticket(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = safe_child(EXPORTS_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export not found")

    now = time.time()
    for old_ticket, (_, expiry) in list(DOWNLOAD_TICKETS.items()):
        if expiry < now:
            DOWNLOAD_TICKETS.pop(old_ticket, None)

    ticket = secrets.token_urlsafe(24)
    DOWNLOAD_TICKETS[ticket] = (filename, now + DOWNLOAD_TICKET_TTL)
    log_action(user["username"], "create_vm_export_download_ticket", filename, "succes")
    return {"ticket": ticket, "expire_dans_s": DOWNLOAD_TICKET_TTL}


@router.get("/vm-exports/download")
def download_vm_export(ticket: str):
    entry = DOWNLOAD_TICKETS.pop(ticket, None)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired download ticket")
    filename, expiry = entry
    if time.time() > expiry:
        raise HTTPException(status_code=401, detail="Download ticket expired")
    path = safe_child(EXPORTS_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
