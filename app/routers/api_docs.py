"""Swagger for the accounts an administrator chose (app/core/api_docs.py)."""

import copy
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.core import api_docs
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/api-docs", tags=["api-docs"])
pages = APIRouter(include_in_schema=False)


@router.get("/acces")
def get_access(user: dict = Depends(get_current_user)):
    """Who may open Swagger, and whether this user may."""
    value = api_docs.access()
    return {"acces": value, "autorise": api_docs.allowed(user, value)}


class AccessSettings(BaseModel):
    acces: Literal["desactive", "admins", "tous"]


@router.put("/acces")
def put_access(payload: AccessSettings, user: dict = Depends(require_role("admin"))):
    value = api_docs.set_access(payload.acces)
    log_action(user["username"], "api_docs_access", "api", "succes", value)
    return {"acces": value, "autorise": api_docs.allowed(user, value)}


@router.post("/ticket")
def create_ticket(user: dict = Depends(get_current_user)):
    """A single-use link to Swagger, for an account allowed to open it."""
    if not api_docs.allowed(user):
        raise HTTPException(status_code=403, detail="Swagger is not open to this account")
    log_action(user["username"], "api_docs_open", "api", "succes")
    return {"url": f"/docs?ticket={api_docs.new_ticket(user['username'])}", "expire_dans_s": api_docs.TICKET_TTL_S}


_REFUSED = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Swagger · Hyperlite</title></head>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>Swagger</h1><p>Open it from Hyperlite, <b>Administration &rsaquo; API &rsaquo; Open Swagger</b>: access is given to the accounts
an administrator chose, for a few hours.</p><p>Ouvrez-le depuis Hyperlite, <b>Administration &rsaquo; API &rsaquo; Ouvrir
Swagger</b> : l'accès est donné aux comptes choisis par un administrateur, pour quelques heures.</p>
<p><a href="/">Hyperlite</a></p></body></html>"""


def _docs_user(request):
    return api_docs.session_user(request.cookies.get(api_docs.COOKIE))


@pages.get("/docs")
def swagger(request: Request, ticket: str | None = None):
    if ticket:
        # The ticket leaves the address at once: the cookie carries the access from now on.
        cookie = api_docs.open_session(ticket)
        if cookie is None:
            return HTMLResponse(_REFUSED, status_code=403)
        response = RedirectResponse("/docs", status_code=303)
        response.set_cookie(
            api_docs.COOKIE,
            cookie,
            max_age=api_docs.SESSION_TTL_S,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response
    if _docs_user(request) is None:
        return HTMLResponse(_REFUSED, status_code=403)
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Hyperlite API · Swagger",
        swagger_js_url="/swagger/swagger-ui-bundle.js",
        swagger_css_url="/swagger/swagger-ui.css",
        swagger_favicon_url="/favicon.svg",
        swagger_ui_parameters={"persistAuthorization": False, "docExpansion": "none", "filter": True},
    )


@pages.get("/openapi.json")
def openapi_schema(request: Request):
    if _docs_user(request) is None:
        return JSONResponse({"detail": "Open Swagger from Administration > API"}, status_code=403)
    return api_docs.with_token_auth(copy.deepcopy(request.app.openapi()))
