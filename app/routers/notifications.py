"""Outbound notification endpoints. Logic lives in
app/core/notifications.py."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import notifications as notif
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/events")
def list_events(user: dict = Depends(get_current_user)):
    """Catalog of notifiable events. Feeds the channel creation form in the UI (one
    checkbox per event)."""
    return notif.NOTIFY_EVENTS


@router.get("/channels")
def list_channels(user: dict = Depends(get_current_user)):
    """List the configured channels.

    Only administrators see a channel's configuration. Webhook URLs routinely
    embed a secret token (Slack, Discord, ...) and email settings expose
    infrastructure details, so other roles only get the channel name, type,
    subscribed events and state. The SMTP password is never returned to
    anyone: notif.list_channels() decrypts it for internal use (sending), so
    it is masked here, like the OIDC client secret."""
    channels = notif.list_channels()
    is_admin = user["role"] == "admin"
    for c in channels:
        if not is_admin:
            c["config"] = {"redacted": True}
        elif c["type"] == "email" and c["config"].get("smtp_password"):
            c["config"]["smtp_password_set"] = True
            c["config"]["smtp_password"] = None
    return channels


class ChannelCreate(BaseModel):
    type: str  # "webhook" | "email"
    name: str
    config: dict
    events: list[str] = []


@router.post("/channels", status_code=201)
def create_channel(payload: ChannelCreate, user: dict = Depends(require_role("admin"))):
    if payload.type not in ("webhook", "email"):
        raise HTTPException(status_code=422, detail="Invalid channel type (webhook or email)")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name required")
    invalid_events = [e for e in payload.events if e not in notif.NOTIFY_EVENTS]
    if invalid_events:
        raise HTTPException(status_code=422, detail=f"Unknown event(s): {', '.join(invalid_events)}")

    channel_id = notif.create_channel(
        payload.type, payload.name.strip(), payload.config, payload.events, user["username"]
    )
    log_action(user["username"], "create_notification_channel", payload.name, "succes")
    return {"id": channel_id}


class ChannelUpdate(BaseModel):
    enabled: bool


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: int, payload: ChannelUpdate, user: dict = Depends(require_role("admin"))):
    notif.set_enabled(channel_id, payload.enabled)
    log_action(user["username"], "update_notification_channel", str(channel_id), "succes")
    return {"message": "Channel updated"}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, user: dict = Depends(require_role("admin"))):
    notif.delete_channel(channel_id)
    log_action(user["username"], "delete_notification_channel", str(channel_id), "succes")
    return {"message": "Channel deleted"}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, user: dict = Depends(require_role("admin"))):
    channel = next((c for c in notif.list_channels() if c["id"] == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        notif.send_to_channel(channel, "Test de notification", "This is a test sent from Hyperlite.")
    except Exception as e:
        log_action(user["username"], "test_notification_channel", channel["name"], "echec", str(e)[:300])
        raise HTTPException(status_code=502, detail=f"Sending failed: {e}") from e
    log_action(user["username"], "test_notification_channel", channel["name"], "succes")
    return {"message": "Test notification sent"}
