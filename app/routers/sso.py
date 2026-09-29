"""OIDC SSO endpoints. All the logic lives in app/core/sso.py; this file only
does the HTTP wiring: the redirects of the Authorization Code flow, and turning
errors (IdP unreachable, invalid token, incomplete configuration...) into a
redirect to the login screen with a clear message rather than a raw 500 that
nobody would see (these are BROWSER redirects, not API calls consumed by the JS
frontend)."""

import json
import urllib.error
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jwt import PyJWTError
from pydantic import BaseModel

from app.core import sso, webauthn_keys
from app.core.audit import log_action
from app.core.database import get_conn
from app.core.error_messages import describe_exception
from app.core.security import create_access_token, create_preauth_token, get_user, require_role

router = APIRouter(prefix="/auth/sso", tags=["sso"])


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
BINDING_COOKIE = "hl_sso_binding"
HANDOFF_COOKIE = "hl_sso_handoff"
COOKIE_PATH = "/auth/sso"


def _set_cookie(response, request, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=COOKIE_PATH,
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
    log_action(user["username"], "update_sso_config", "sso", "succes")
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
        log_action(user["username"], "test_sso", "sso", "echec", describe_exception(e))
        return {"ok": False, "detail": _discovery_failure(e)}
    missing = [k for k in ("authorization_endpoint", "token_endpoint", "jwks_uri") if not doc.get(k)]
    ok = not missing
    log_action(user["username"], "test_sso", "sso", "succes" if ok else "echec")
    return {
        "ok": ok,
        "issuer": doc.get("issuer"),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "detail": None if ok else f"Discovery document incomplete: missing {', '.join(missing)}",
    }


@router.get("/login")
def sso_login(request: Request):
    config = sso.get_config()
    if not config or not config["enabled"]:
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    if config.get("client_secret_unreadable"):
        raise HTTPException(
            status_code=503,
            detail="The stored SSO client secret cannot be decrypted (the encryption key changed): enter it again",
        )
    if not (config["issuer"] and config["client_id"] and config["client_secret"] and config["redirect_uri"]):
        raise HTTPException(status_code=400, detail="Incomplete SSO configuration")
    try:
        doc = sso.discover(config["issuer"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"IdP unreachable: {e}") from e
    state, nonce, binding = sso.create_state()
    response = RedirectResponse(sso.build_authorize_url(config, doc, state, nonce))
    _set_cookie(response, request, BINDING_COOKIE, binding, sso.STATE_TTL_S)
    return response


@router.get("/callback")
def sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        log_action("system", "login", "auth", "echec", f"SSO refused by the IdP: {error_description or error}")
        return _redirect_error(error_description or error)
    if not code or not state:
        return _redirect_error("Incomplete response from the IdP")

    nonce = sso.consume_state(state, request.cookies.get(BINDING_COOKIE))
    if nonce is None:
        # Expired, replayed, or started from another browser (a callback link someone sent).
        log_action("system", "login", "auth", "echec", "SSO: unknown, expired or foreign sign-in state")
        return _redirect_error("Login session expired, try again")

    config = sso.get_config()
    if not config or not config["enabled"]:
        return _redirect_error("SSO is disabled")

    try:
        doc = sso.discover(config["issuer"])
        tokens = sso.exchange_code(config, doc, code)
        id_token = tokens["id_token"]
        claims = sso.validate_id_token(config, doc, id_token, nonce)
    except (PyJWTError, KeyError) as e:
        log_action("system", "login", "auth", "echec", f"SSO: invalid identity token ({e})")
        return _redirect_error("Invalid identity token")
    except Exception as e:
        log_action("system", "login", "auth", "echec", f"SSO: IdP unreachable or invalid response ({e})")
        return _redirect_error("Unable to contact the IdP")

    username = sso.resolve_username(claims)
    if not username:
        return _redirect_error("The IdP provided no usable identifier")
    role = sso.resolve_role(config, claims)

    try:
        db_user = sso.provision_user(username, role, subject=claims.get("sub"))
    except sso.LocalAccountConflict:
        log_action(username, "login", "auth", "echec", "SSO: this name already matches a local account")
        return _redirect_error("This username already matches a local account")
    except sso.SubjectConflict:
        log_action(username, "login", "auth", "echec", "SSO: this name belongs to another identity of the provider")
        return _redirect_error("This username belongs to another identity: ask an administrator")

    log_action(db_user["username"], "login", "auth", "succes", "SSO identity validated")
    response = RedirectResponse("/?sso=1")
    response.delete_cookie(BINDING_COOKIE, path=COOKIE_PATH)
    _set_cookie(response, request, HANDOFF_COOKIE, sso.create_handoff(db_user["username"]), sso.HANDOFF_TTL_S)
    return response


@router.post("/exchange")
def sso_exchange(request: Request):
    """The dashboard's session after an SSO sign-in, from the one-time handoff cookie the callback set. When the
    account has a second factor, the answer is the same as a password sign-in's (require_2fa): the identity
    provider proves who signs in, not the second factor this account chose to require."""
    username = sso.consume_handoff(request.cookies.get(HANDOFF_COOKIE))
    user = get_user(username) if username else None
    if user is None:
        response = JSONResponse(status_code=401, content={"detail": "SSO sign-in expired, try again"})
        response.delete_cookie(HANDOFF_COOKIE, path=COOKIE_PATH)
        return response
    methods = [m for m, on in (("totp", user["totp_enabled"]), ("webauthn", webauthn_keys.has_keys(username))) if on]
    if methods:
        body = {"require_2fa": True, "pre_auth_token": create_preauth_token(username), "methods": methods}
        log_action(username, "login", "auth", "succes", "SSO validated, second factor required")
    else:
        token = create_access_token({"sub": username, "role": user["role"]})
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE username = ?", (datetime.now(UTC).isoformat(), username)
            )
            conn.commit()
        log_action(username, "login", "auth", "succes", "SSO login")
        body = {
            "access_token": token,
            "token_type": "bearer",
            "role": user["role"],
            "username": username,
            "password_change_required": bool(user.get("must_change_password")),
        }
    response = JSONResponse(content=body)
    response.delete_cookie(HANDOFF_COOKIE, path=COOKIE_PATH)
    return response
