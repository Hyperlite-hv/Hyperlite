"""Sign-in rules, exclusions and scheduled access reviews (docs/design/governance.md, sections 4 to 6)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import access_review, signin_rules
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role

router = APIRouter(tags=["governance"])


# ---- Sign-in rules ----


class SigninRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subject_type: Literal["all", "role", "group", "user"] = "all"
    subject_id: str | None = Field(None, max_length=256)
    networks: list[str] = Field(default_factory=list, max_length=100)
    require_2fa: bool = False
    days: list[str] = Field(default_factory=list, max_length=7)
    hours: str | None = Field(None, max_length=11)
    mode: Literal["watch", "enforce"] = "watch"


@router.get("/signin-rules")
def list_signin_rules(user: dict = Depends(require_role("admin"))):
    return listing(signin_rules.list_rules())


def _save_rule(payload, user, rule_id=None):
    try:
        rule = signin_rules.save(payload.model_dump(), user["username"], rule_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if rule is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "signin_rule_saved", rule["name"], "success", f"mode {rule['mode']}")
    return rule


@router.post("/signin-rules", status_code=201)
def create_signin_rule(payload: SigninRuleIn, user: dict = Depends(require_role("admin"))):
    return _save_rule(payload, user)


@router.put("/signin-rules/{rule_id}")
def update_signin_rule(rule_id: str, payload: SigninRuleIn, user: dict = Depends(require_role("admin"))):
    return _save_rule(payload, user, rule_id)


@router.delete("/signin-rules/{rule_id}")
def delete_signin_rule(rule_id: str, user: dict = Depends(require_role("admin"))):
    if not signin_rules.delete(rule_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "signin_rule_deleted", rule_id, "success")
    return {"message": "Rule deleted"}


# ---- Exclusions ----


class ExclusionIn(BaseModel):
    subject_type: Literal["user", "group"]
    subject_id: str = Field(min_length=1, max_length=256)
    privilege: str = Field("*", min_length=1, max_length=128)
    resource_type: str = Field(min_length=1, max_length=32)
    resource_id: str = Field("*", min_length=1, max_length=256)
    reason: str = Field("", max_length=500)


@router.get("/access/exclusions")
def list_exclusions(user: dict = Depends(require_role("admin"))):
    return listing(perm.list_exclusions())


@router.post("/access/exclusions", status_code=201)
def create_exclusion(payload: ExclusionIn, user: dict = Depends(require_role("admin"))):
    from app.core import statd

    if payload.resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    if statd.members_without("acl_exclusions"):
        raise HTTPException(status_code=409, detail="Update every node before making exclusions")
    resource_id = "*" if payload.resource_type == "cluster" else payload.resource_id
    try:
        exclusion_id = perm.create_exclusion(
            payload.subject_type, payload.subject_id, payload.privilege, payload.resource_type, resource_id,
            payload.reason, user["username"],
        )  # fmt: skip
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(
        user["username"],
        "create_exclusion",
        f"{payload.subject_type}:{payload.subject_id} -{payload.privilege} @ {payload.resource_type}:{resource_id}",
        "success",
        payload.reason,
    )
    return {"id": exclusion_id}


@router.delete("/access/exclusions/{exclusion_id}")
def delete_exclusion(exclusion_id: int, user: dict = Depends(require_role("admin"))):
    perm.delete_exclusion(exclusion_id)
    log_action(user["username"], "delete_exclusion", str(exclusion_id), "success")
    return {"message": "Exclusion removed"}


# ---- Scheduled access reviews ----


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    every_days: int = Field(90, ge=1, le=366)
    deadline_days: int = Field(14, ge=1, le=90)
    reviewers: list[str] = Field(default_factory=list, max_length=50)
    remove_undecided: bool = False


def _campaign_or_404(campaign_id, user):
    c = next((c for c in access_review.campaigns() if c["id"] == campaign_id), None)
    if c is None:
        raise HTTPException(status_code=404, detail="Not found")
    if user["role"] != "admin" and user["username"] not in c["reviewers"]:
        raise HTTPException(status_code=403, detail="Only an administrator or a reviewer of this review may do this")
    return c


@router.get("/access/campaigns")
def list_campaigns(user: dict = Depends(get_current_user)):
    """An administrator's every review; anyone else the reviews it is a reviewer of."""
    return listing(
        [c for c in access_review.campaigns() if user["role"] == "admin" or user["username"] in c["reviewers"]]
    )


@router.post("/access/campaigns", status_code=201)
def create_campaign(payload: CampaignIn, user: dict = Depends(require_role("admin"))):
    if payload.deadline_days >= payload.every_days:
        raise HTTPException(status_code=422, detail="The deadline must come before the next round")
    c = access_review.save_campaign(payload.model_dump(), user["username"])
    log_action(user["username"], "create_review_campaign", c["name"], "success")
    return c


@router.put("/access/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, payload: CampaignIn, user: dict = Depends(require_role("admin"))):
    if payload.deadline_days >= payload.every_days:
        raise HTTPException(status_code=422, detail="The deadline must come before the next round")
    c = access_review.save_campaign(payload.model_dump(), user["username"], campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Not found")
    return c


@router.delete("/access/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str, user: dict = Depends(require_role("admin"))):
    if not access_review.delete_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "delete_review_campaign", campaign_id, "success")
    return {"message": "Review deleted"}


@router.get("/access/campaigns/{campaign_id}/items")
def campaign_items(campaign_id: str, user: dict = Depends(get_current_user)):
    _campaign_or_404(campaign_id, user)
    return listing(access_review.items(campaign_id))


class DecisionIn(BaseModel):
    decision: Literal["keep", "remove"]


@router.post("/access/campaigns/{campaign_id}/items/{item_id}")
def decide_item(campaign_id: str, item_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    c = _campaign_or_404(campaign_id, user)
    if not c["round_due_at"]:
        raise HTTPException(status_code=409, detail="This review has no round open")
    done = access_review.decide(campaign_id, item_id, payload.decision, user["username"])
    if done is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "access_review_decision", done["summary"], "success", payload.decision)
    return done
