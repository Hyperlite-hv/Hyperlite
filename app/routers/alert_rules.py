"""Alert rules (#348, app/core/alert_rules.py): read by every signed-in user, written by administrators."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import alert_rules
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role

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
def create_rule(payload: AlertRule, user: dict = Depends(require_role("admin"))):
    try:
        rule = alert_rules.create(payload.model_dump(), user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "alert_rule_create", rule["name"], "success", f"id={rule['id']}")
    return rule


@router.put("/{rule_id}")
def update_rule(rule_id: str, payload: AlertRule, user: dict = Depends(require_role("admin"))):
    try:
        rule = alert_rules.update(rule_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if rule is None:
        raise HTTPException(status_code=404, detail="No such alert rule")
    log_action(user["username"], "alert_rule_update", rule["name"], "success", f"id={rule_id}")
    return rule


@router.put("/{rule_id}/enabled")
def enable_rule(rule_id: str, payload: AlertRuleEnabled, user: dict = Depends(require_role("admin"))):
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
def delete_rule(rule_id: str, user: dict = Depends(require_role("admin"))):
    rule = alert_rules.rule(rule_id)
    if not rule or not alert_rules.delete(rule_id):
        raise HTTPException(status_code=404, detail="No such alert rule")
    log_action(user["username"], "alert_rule_delete", rule["name"], "success", f"id={rule_id}")
