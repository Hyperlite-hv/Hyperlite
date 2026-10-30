"""OIDC SSO endpoints. All the logic lives in app/core/sso.py; this file only
does the HTTP wiring: the redirects of the Authorization Code flow, and turning
errors (IdP unreachable, invalid token, incomplete configuration...) into a
redirect to the login screen with a clear message rather than a raw 500 that
nobody would see (these are BROWSER redirects, not API calls consumed by the JS
frontend)."""

import json
import logging
import urllib.error
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.core import sign_in, sso
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.security import require_role
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["sso"])
# What a browser opens, not a client of the API: the sign-in redirection, and the callback the identity provider was
# given (its redirect URI). They stay at the root, outside /api/v1, so an IdP configured before keeps working.
pages = APIRouter(prefix="/auth/sso", tags=["sso"])


class SSOConfigIn(BaseModel):
    enabled: bool
    issuer: str = ""
    client_id: str = ""
    # None = do not change the existing secret: it avoids forcing it to be re-entered
    # at every edit of the other fields (the UI never displays it in clear text again,
    # see GET /config below).
    client_secret: str | None = None
    redirect_uri: str = ""
    scope: str = "openid profile email groups"
    group_claim: str = "groups"
    admin_groups: str = ""


# Both cookies are HttpOnly (no script reads them), limited to the SSO endpoints, and SameSite=Lax: sent on the
# top-level redirect back from the identity provider, never on a request another site makes in the background.
# The binding goes from the sign-in to the callback (both at the root); the hand-off from the callback to the exchange
# the dashboard calls, under the API's prefix.
BINDING_COOKIE = "hl_sso_binding"
HANDOFF_COOKIE = "hl_sso_handoff"
COOKIE_PATH = "/auth/sso"
HANDOFF_PATH = "/api/v1/auth/sso"


def _set_cookie(response, request, name, value, max_age, path=COOKIE_PATH):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _redirect_error(message):
    response = RedirectResponse("/?" + urlencode({"sso_error": message}))
    response.delete_cookie(BINDING_COOKIE, path=COOKIE_PATH)
    return response


@router.get("/status")
def sso_status():
    """Public, WITHOUT authentication: the login screen must know whether to show the
    SSO button before anyone is signed in. It never returns the configuration
    itself (see /config, admin-only)."""
    config = sso.get_config()
    enabled = bool(config and config["enabled"] and config["issuer"] and config["client_id"])
    return {"enabled": enabled}


@router.get("/config")
def get_sso_config(user: dict = Depends(require_role("admin"))):
    config = sso.get_config() or {}
    config = dict(config)
    has_secret = bool(config.pop("client_secret", None))
    config["client_secret_set"] = has_secret or bool(config.get("client_secret_unreadable"))
    return config


@router.put("/config")
def put_sso_config(payload: SSOConfigIn, user: dict = Depends(require_role("admin"))):
    fields = payload.model_dump(exclude={"client_secret"})
    fields["enabled"] = int(payload.enabled)
    if payload.client_secret:  # None ou "" -> secret existant conserve
        fields["client_secret"] = payload.client_secret
    sso.set_config(**fields)
    log_action(user["username"], "update_sso_config", "sso", "success")
    return {"message": "SSO configuration updated"}


def _discovery_failure(exc):
    """Fixed, user-facing reason for a failed discovery: the exception text itself only
    goes to the audit log, never to the browser."""
    if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
        return "The issuer is not a valid http(s) URL."
    if isinstance(exc, json.JSONDecodeError):
        return "The provider did not answer with an OIDC discovery document."
    if isinstance(exc, urllib.error.HTTPError):
        return "The provider answered with an HTTP error: check the issuer URL."
    if isinstance(exc, TimeoutError):
        return "The provider did not answer in time."
    return "The provider could not be reached from this server."


@router.post("/test")
def test_sso(user: dict = Depends(require_role("admin"))):
    """Reads the OIDC discovery document of the saved issuer, so the admin can check
    the provider before enabling SSO (a wrong issuer would otherwise only show up as a
    failed sign-in). Only the stored issuer is contacted: the endpoint takes no URL."""
    issuer = (sso.get_config() or {}).get("issuer") or ""
    if not issuer:
        return {"ok": False, "detail": "No issuer saved yet: save the configuration first."}
    try:
        doc = sso.discover(issuer)
    except Exception as e:
        log_action(user["username"], "test_sso", "sso", "failed", describe_exception(e))
        return {"ok": False, "detail": _discovery_failure(e)}
    missing = [k for k in ("authorization_endpoint", "token_endpoint", "jwks_uri") if not doc.get(k)]
    ok = not missing
    log_action(user["username"], "test_sso", "sso", "success" if ok else "failed")
    return {
        "ok": ok,
        "issuer": doc.get("issuer"),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "detail": None if ok else f"Discovery document incomplete: missing {', '.join(missing)}",
    }


def _ask(op, **args):
    """sign_in's `op`, done by hyperlite-daemon: the client secret is opened, and the account written, there."""
    try:
        return sign_in.ask(op, **args)
    except DaemonError as e:
        logger.error("SSO sign-in (%s) unavailable: %s", op, e.detail)
        return None


@pages.get("/login")
def sso_login(request: Request):
    answer = _ask("sso_start")
    if answer is None:
        raise HTTPException(status_code=503, detail="Signing in is unavailable: hyperlite-daemon does not answer")
    if answer["result"] != "redirect":
        raise HTTPException(status_code=answer["status"], detail=answer["detail"])
    response = RedirectResponse(answer["url"])
    _set_cookie(response, request, BINDING_COOKIE, answer["binding"], sso.STATE_TTL_S)
    return response


@pages.get("/callback")
def sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        log_action("system", "login", "auth", "failed", f"SSO refused by the IdP: {error_description or error}")
        return _redirect_error(error_description or error)
    if not code or not state:
        return _redirect_error("Incomplete response from the IdP")
    answer = _ask("sso_callback", state=state, binding=request.cookies.get(BINDING_COOKIE), code=code)
    if answer is None:
        return _redirect_error("Signing in is unavailable: hyperlite-daemon does not answer")
    if answer["result"] != "handoff":
        if answer.get("log"):
            log_action(answer.get("log_user") or "system", "login", "auth", "failed", answer["log"])
        return _redirect_error(answer["detail"])
    log_action(answer["username"], "login", "auth", "success", "SSO identity validated")
    response = RedirectResponse("/?sso=1")
    response.delete_cookie(BINDING_COOKIE, path=COOKIE_PATH)
    _set_cookie(response, request, HANDOFF_COOKIE, answer["handoff"], sso.HANDOFF_TTL_S, HANDOFF_PATH)
    return response


@router.post("/exchange")
def sso_exchange(request: Request):
    """The dashboard's session after an SSO sign-in, from the one-time handoff cookie the callback set. When the
    account has a second factor, the answer is the same as a password sign-in's (require_2fa): the identity
    provider proves who signs in, not the second factor this account chose to require."""
    # The one-time code is consumed by hyperlite-daemon, which signs (app/core/sign_in.py).
    try:
        answer = sign_in.ask("sso", handoff=request.cookies.get(HANDOFF_COOKIE))
    except DaemonError as e:
        logger.error("SSO sign-in unavailable: %s", e.detail)
        return JSONResponse(
            status_code=503,
            content={"detail": "Signing in is unavailable: hyperlite-daemon does not answer", "code": "unavailable"},
        )
    if answer["result"] == "expired":
        response = JSONResponse(
            status_code=401, content={"detail": "SSO sign-in expired, try again", "code": "unauthorized"}
        )
        response.delete_cookie(HANDOFF_COOKIE, path=HANDOFF_PATH)
        return response
    user = answer["user"]
    username = user["username"]
    if answer["result"] == "second_factor":
        body = {"require_2fa": True, "preauth_token": answer["preauth_token"], "methods": answer["methods"]}
        log_action(username, "login", "auth", "success", "SSO validated, second factor required")
    else:
        log_action(username, "login", "auth", "success", "SSO login")
        body = {
            "access_token": answer["token"],
            "token_type": "bearer",
            "role": user["role"],
            "username": username,
            "password_change_required": user["password_change_required"],
        }
    response = JSONResponse(content=body)
    response.delete_cookie(HANDOFF_COOKIE, path=HANDOFF_PATH)
    return response
