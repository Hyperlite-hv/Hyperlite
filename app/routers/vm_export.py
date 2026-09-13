"""Endpoints export de VM par fichier disque (chantier 23, 2026-09-13).
Logique reelle dans app/core/vm_export.py (reutilise app/core/backups.py) --
ce fichier valide les entrees, verifie les droits et orchestre en tache de
fond, meme schema que app/routers/backups.py.

Telechargement par ticket a usage unique (meme mecanisme que les terminaux
web SSH, app/routers/vms.py::create_terminal_ticket) plutot qu'un simple GET
protege par JWT : l'authentification de l'app est un Bearer token (voir
app/core/security.py), impossible a joindre a un lien <a href>/telechargement
navigateur classique -- il faut un ticket courte duree, obtenu par un appel
authentifie prealable, encode dans l'URL elle-meme."""
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.audit import log_action
from app.core.security import require_role, require_vm_privilege
from app.core.vm_export import EXPORTS_DIR, list_exports, run_export

router = APIRouter(tags=["vm-export"])

DOWNLOAD_TICKETS = {}
DOWNLOAD_TICKET_TTL = 60


@router.get("/vm-exports")
def list_vm_exports(user: dict = Depends(require_role("admin"))):
    return list_exports()


@router.post("/vms/{name}/export", status_code=202)
def export_vm(name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    # Reutilise le privilege vm.snapshot (proteger l'etat d'une VM, meme
    # esprit que les sauvegardes, voir backups.py) plutot qu'un nouveau
    # privilege dedie.
    def job():
        try:
            run_export(name, username=user["username"])
        except Exception:
            pass  # deja journalise/trace dans run_export (task + audit_log)

    threading.Thread(target=job, daemon=True).start()
    log_action(user["username"], "export_vm_requested", name, "succes")
    return {"message": f"Export de '{name}' lancé en arrière-plan"}


@router.delete("/vm-exports/{filename}")
def delete_vm_export(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export introuvable")
    path.unlink()
    log_action(user["username"], "delete_vm_export", filename, "succes")
    return {"nom": filename, "supprime": True}


@router.post("/vm-exports/{filename}/download-ticket")
def create_download_ticket(filename: str, user: dict = Depends(require_role("admin"))):
    filename = Path(filename).name
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export introuvable")

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
        raise HTTPException(status_code=401, detail="Ticket de téléchargement invalide ou expiré")
    filename, expiry = entry
    if time.time() > expiry:
        raise HTTPException(status_code=401, detail="Ticket de téléchargement expiré")
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export introuvable")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
