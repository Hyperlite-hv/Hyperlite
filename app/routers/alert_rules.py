"""Alert rules (#348, app/core/alert_rules.py): read by every signed-in user, written by administrators."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import alert_rules
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_area

router = APIRouter(prefix="/alert-rules", tags=["alerts"])


class AlertRule(BaseModel):
    name: str = Field(max_length=80)
    enabled: bool = True
    scope: str
    node: str | None = None
    vm: str | None = None
    metric: str
    operator: str
    threshold: float
    window_min: int = 5
    severity: str = "warning"
    channels: list[int] = Field(default_factory=list, max_length=alert_rules.MAX_CHANNELS)


class AlertRuleEnabled(BaseModel):
    enabled: bool


@router.get("")
def list_rules(user: dict = Depends(get_current_user)):
    return listing(alert_rules.rules())


@router.get("/options")
def options(user: dict = Depends(get_current_user)):
    """What a rule may say: the forms list these."""
    return {
        "metrics": {k: unit for k, (unit, _) in alert_rules.METRICS.items()},
        "operators": list(alert_rules.OPERATORS),
        "scopes": list(alert_rules.SCOPES),
        "severities": list(alert_rules.SEVERITIES),
        "windows": list(alert_rules.WINDOWS),
    }


@router.post("", status_code=201)
def create_rule(payload: AlertRule, user: dict = Depends(require_area("alert.manage"))):
    try:
        rule = alert_rules.create(payload.model_dump(), user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "alert_rule_create", rule["name"], "success", f"id={rule['id']}")
    return rule


@router.put("/{rule_id}")
def update_rule(rule_id: str, payload: AlertRule, user: dict = Depends(require_area("alert.manage"))):
    try:
        rule = alert_rules.update(rule_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if rule is None:
        raise HTTPException(status_code=404, detail="No such alert rule")
    log_action(user["username"], "alert_rule_update", rule["name"], "success", f"id={rule_id}")
    return rule


@router.put("/{rule_id}/enabled")
def enable_rule(rule_id: str, payload: AlertRuleEnabled, user: dict = Depends(require_area("alert.manage"))):
    rule = alert_rules.set_enabled(rule_id, payload.enabled)
    if rule is None:
        raise HTTPException(status_code=404, detail="No such alert rule")
    log_action(
        user["username"],
        "alert_rule_enable" if payload.enabled else "alert_rule_disable",
        rule["name"],
        "success",
        f"id={rule_id}",
    )
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: str, user: dict = Depends(require_area("alert.manage"))):
    rule = alert_rules.rule(rule_id)
    if not rule or not alert_rules.delete(rule_id):
        raise HTTPException(status_code=404, detail="No such alert rule")
    log_action(user["username"], "alert_rule_delete", rule["name"], "success", f"id={rule_id}")


silences_router = APIRouter(prefix="/alert-silences", tags=["alerts"])


class SilenceIn(BaseModel):
    target_kind: Literal["all", "node", "vm", "pool", "rule"] = "all"
    target: str | None = Field(None, max_length=256)
    starts_at: datetime
    ends_at: datetime
    reason: str = Field("", max_length=500)


@silences_router.get("")
def list_silences(user: dict = Depends(require_area("alert.view"))):
    return listing(alert_rules.silences())


@silences_router.post("", status_code=201)
def add_silence(payload: SilenceIn, user: dict = Depends(require_area("alert.manage"))):
    from datetime import UTC

    start = (payload.starts_at if payload.starts_at.tzinfo else payload.starts_at.replace(tzinfo=UTC)).astimezone(UTC)
    end = (payload.ends_at if payload.ends_at.tzinfo else payload.ends_at.replace(tzinfo=UTC)).astimezone(UTC)
    try:
        row = alert_rules.add_silence(payload.target_kind, payload.target, start.isoformat(), end.isoformat(),
                                      payload.reason, user["username"])  # fmt: skip
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "alert_silence", f"{payload.target_kind}:{payload.target or '*'}", "success",
               f"until {end.isoformat()[:16]} {payload.reason}".strip())  # fmt: skip
    return row


@silences_router.delete("/{silence_id}")
def remove_silence(silence_id: str, user: dict = Depends(require_area("alert.manage"))):
    if not alert_rules.remove_silence(silence_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "alert_silence_removed", silence_id, "success")
    return {"message": "Silence removed"}
