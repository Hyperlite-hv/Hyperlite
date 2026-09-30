"""The API's interactive documentation for signed-in users (app/core/api_docs.py)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import api_docs
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/api-docs", tags=["api-docs"])


@router.get("/acces")
def get_access(user: dict = Depends(get_current_user)):
    """Who may read the documentation, and whether this user may."""
    value = api_docs.access()
    return {"acces": value, "autorise": api_docs.allowed(user, value)}


class AccessSettings(BaseModel):
    acces: Literal["desactive", "admins", "tous"]


@router.put("/acces")
def put_access(payload: AccessSettings, user: dict = Depends(require_role("admin"))):
    value = api_docs.set_access(payload.acces)
    log_action(user["username"], "api_docs_access", "api", "succes", value)
    return {"acces": value, "autorise": api_docs.allowed(user, value)}


@router.get("/schema")
def get_schema(request: Request, user: dict = Depends(get_current_user)):
    """The OpenAPI schema of this Hyperlite, generated even when the public /openapi.json is off."""
    if not api_docs.allowed(user):
        raise HTTPException(status_code=403, detail="The API documentation is not open to this account")
    return request.app.openapi()
