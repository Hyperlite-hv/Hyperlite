"""Endpoints de notifications sortantes (chantier 28). Logique dans
app/core/notifications.py."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import notifications as notif
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/events")
def list_events(user: dict = Depends(get_current_user)):
    """Catalogue des evenements notifiables -- alimente le formulaire de
    creation de canal cote UI (case a cocher par evenement)."""
    return notif.NOTIFY_EVENTS


@router.get("/channels")
def list_channels(user: dict = Depends(get_current_user)):
    return notif.list_channels()


class ChannelCreate(BaseModel):
    type: str  # "webhook" | "email"
    name: str
    config: dict
    events: list[str] = []


@router.post("/channels", status_code=201)
def create_channel(payload: ChannelCreate, user: dict = Depends(require_role("admin"))):
    if payload.type not in ("webhook", "email"):
        raise HTTPException(status_code=422, detail="Type de canal invalide (webhook ou email)")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Nom requis")
    invalid_events = [e for e in payload.events if e not in notif.NOTIFY_EVENTS]
    if invalid_events:
        raise HTTPException(status_code=422, detail=f"Événement(s) inconnu(s) : {', '.join(invalid_events)}")

    channel_id = notif.create_channel(payload.type, payload.name.strip(), payload.config, payload.events, user["username"])
    log_action(user["username"], "create_notification_channel", payload.name, "succes")
    return {"id": channel_id}


class ChannelUpdate(BaseModel):
    enabled: bool


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: int, payload: ChannelUpdate, user: dict = Depends(require_role("admin"))):
    notif.set_enabled(channel_id, payload.enabled)
    log_action(user["username"], "update_notification_channel", str(channel_id), "succes")
    return {"message": "Canal mis à jour"}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, user: dict = Depends(require_role("admin"))):
    notif.delete_channel(channel_id)
    log_action(user["username"], "delete_notification_channel", str(channel_id), "succes")
    return {"message": "Canal supprimé"}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, user: dict = Depends(require_role("admin"))):
    channel = next((c for c in notif.list_channels() if c["id"] == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="Canal introuvable")
    try:
        notif.send_to_channel(channel, "Test de notification", "Ceci est un test envoyé depuis Hyperlite.")
    except Exception as e:
        log_action(user["username"], "test_notification_channel", channel["name"], "echec", str(e)[:300])
        raise HTTPException(status_code=502, detail=f"Échec de l'envoi : {e}")
    log_action(user["username"], "test_notification_channel", channel["name"], "succes")
    return {"message": "Notification de test envoyée"}
