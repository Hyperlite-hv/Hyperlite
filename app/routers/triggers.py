"""Triggers: an automation job run when something happens (app/core/triggers.py, docs/design/console-2.md section 6)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import triggers
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import require_role

router = APIRouter(prefix="/job-triggers", tags=["jobs"])


class TriggerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event: str
    filter: str | None = Field(None, max_length=200)
    job_id: int
    pass_target: bool = True
    cooldown_min: int = Field(30, ge=1, le=10080)
    enabled: bool = True


def _answer(fn, *args):
    try:
        return fn(*args)
    except triggers.TriggerError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("")
def list_triggers(user: dict = Depends(require_role("admin"))):
    """Every trigger with its last firing, and the events a trigger may follow."""
    return {"items": triggers.triggers(), "events": list(triggers.EVENTS)}


@router.post("", status_code=201)
def create_trigger(payload: TriggerIn, user: dict = Depends(require_role("admin"))):
    t = _answer(triggers.save, payload.model_dump(), user["username"])
    log_action(user["username"], "trigger_create", t["name"], "success", f"{t['event']} -> job {t['job_id']}")
    return t


@router.put("/{trigger_id}")
def update_trigger(trigger_id: str, payload: TriggerIn, user: dict = Depends(require_role("admin"))):
    t = _answer(triggers.save, payload.model_dump(), user["username"], trigger_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "trigger_change", t["name"], "success")
    return t


@router.delete("/{trigger_id}")
def delete_trigger(trigger_id: str, user: dict = Depends(require_role("admin"))):
    if not triggers.delete(trigger_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "trigger_delete", trigger_id, "success")
    return {"message": "Trigger deleted"}


@router.get("/firings")
def list_firings(trigger_id: str | None = None, user: dict = Depends(require_role("admin"))):
    return listing(triggers.firings(trigger_id))
