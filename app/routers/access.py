"""What an account may do, and why (docs/design/access-2.md, sections 2 and 3): its own effective privileges for the
dashboard, the explanation of one account's rights on one object, and the accounts and groups a delegate assigns to."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import access_requests
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user
from app.services import account_service

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/me")
def my_access(user: dict = Depends(get_current_user)):
    """The signed-in account's area privileges where this node answers and its privileges per perimeter: the
    dashboard shows the actions it may take from this instead of guessing from the global role."""
    from app.core import cluster_view, self_node

    here = self_node.name()
    nodes = sorted({here, *(m["name"] for m in cluster_view.members())})
    return {"username": user["username"], "role": user["role"], "node": here, "nodes": nodes, **perm.effective(user)}


@router.get("/explain")
def explain(
    resource_type: str = Query(...),
    resource_id: str = Query(...),
    username: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """Each privilege `username` holds on that object and the assignments that grant it. An administrator, or
    access.view on the object or a perimeter above it; any account may ask about itself."""
    if resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    if username != user["username"] and not perm.may_view_access(user, resource_type, resource_id):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.view)")
    target = account_service.get(username)
    if not target:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    return {
        "username": username,
        "role": target["role"],
        "resource_type": resource_type,
        "resource_id": "*" if resource_type == "cluster" else resource_id,
        "perimeters": [
            {"resource_type": t, "resource_id": i} for t, i in perm.perimeters_of(resource_type, resource_id)
        ],
        "privileges": perm.explain(target, resource_type, resource_id),
    }


@router.get("/subjects")
def subjects(user: dict = Depends(get_current_user)):
    """The accounts (not administrators: they hold every right already) and the groups an assignment can name, for
    an administrator or a delegate: GET /users stays an administrator's."""
    if user["role"] != "admin" and not perm.holds_anywhere(user, "access.manage"):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.manage)")
    users = [
        {"username": u["username"], "role": u["role"]}
        for u in account_service._store().list_summary()
        if u["role"] != "admin"
    ]
    return {"users": users, "groups": [{"id": g["id"], "name": g["name"]} for g in perm.list_groups()]}


# ---- On-demand rights (docs/design/access-2.md, section 6) ----


class EligibilityIn(BaseModel):
    subject_type: Literal["user", "group"]
    subject_id: str = Field(min_length=1, max_length=256)
    role: str = Field(min_length=1, max_length=128)
    resource_type: str = Field(min_length=1, max_length=32)
    resource_id: str = Field("*", min_length=1, max_length=256)
    max_minutes: int = Field(60, ge=5, le=60 * 24 * 31)
    approval: Literal["auto", "approval"] = "approval"


@router.get("/eligibility")
def list_eligibility(user: dict = Depends(get_current_user)):
    """Every eligibility for an administrator; the account's own, and those on a perimeter it may read the access
    of, for anyone else."""
    groups = set(perm.get_user_groups(user["username"]))
    rows = []
    for e in perm.list_eligibility():
        mine = perm._subject_matches(e, user["username"], groups)
        if mine or perm.may_view_access(user, e["resource_type"], e["resource_id"]):
            rows.append({**e, "mine": mine})
    return listing(rows)


@router.post("/eligibility", status_code=201)
def create_eligibility(payload: EligibilityIn, user: dict = Depends(get_current_user)):
    from app.core import statd

    if payload.resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    resource_id = "*" if payload.resource_type == "cluster" else payload.resource_id
    refusal = perm.grant_refusal(
        user, payload.subject_type, payload.subject_id, payload.role, payload.resource_type, resource_id
    )
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    if statd.members_without("acl_terms"):
        raise HTTPException(status_code=409, detail="Update every node before giving on-demand rights")
    if payload.subject_type == "user" and not account_service.get(payload.subject_id):
        raise HTTPException(status_code=404, detail=f"User '{payload.subject_id}' not found")
    try:
        eligibility_id = perm.create_eligibility(
            payload.subject_type, payload.subject_id, payload.role, payload.resource_type, resource_id,
            payload.max_minutes, payload.approval, user["username"],
        )  # fmt: skip
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(
        user["username"],
        "create_eligibility",
        f"{payload.subject_type}:{payload.subject_id} -> {payload.role} @ {payload.resource_type}:{resource_id}",
        "success",
        f"up to {payload.max_minutes} min, {payload.approval}",
    )
    return {"id": eligibility_id}


@router.delete("/eligibility/{eligibility_id}")
def delete_eligibility(eligibility_id: int, user: dict = Depends(get_current_user)):
    row = next((e for e in perm.list_eligibility() if e["id"] == eligibility_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    refusal = perm.grant_refusal(
        user, row["subject_type"], row["subject_id"], row["role"], row["resource_type"], row["resource_id"]
    )
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    perm.delete_eligibility(eligibility_id)
    log_action(user["username"], "delete_eligibility", str(eligibility_id), "success")
    return {"message": "Eligibility removed"}


class RequestIn(BaseModel):
    eligibility_id: int
    minutes: int = Field(ge=5, le=60 * 24 * 31)
    reason: str = Field(min_length=1, max_length=500)
    code: str | None = Field(None, max_length=16)


def _answer(fn, *args):
    try:
        return fn(*args)
    except access_requests.RequestError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/requests")
def list_requests(user: dict = Depends(get_current_user)):
    return listing(access_requests.listing(user))


@router.post("/requests", status_code=201)
def ask(payload: RequestIn, user: dict = Depends(get_current_user)):
    return _answer(access_requests.ask, user, payload.eligibility_id, payload.minutes, payload.reason, payload.code)


@router.post("/requests/{request_id}/approve")
def approve(request_id: str, user: dict = Depends(get_current_user)):
    return _answer(access_requests.decide, user, request_id, True)


@router.post("/requests/{request_id}/refuse")
def refuse(request_id: str, user: dict = Depends(get_current_user)):
    return _answer(access_requests.decide, user, request_id, False)


@router.post("/requests/{request_id}/cancel")
def cancel(request_id: str, user: dict = Depends(get_current_user)):
    return _answer(access_requests.cancel, user, request_id)
