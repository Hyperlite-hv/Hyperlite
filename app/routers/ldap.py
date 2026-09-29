"""LDAP / Active Directory sign-in settings (app/core/ldap_auth.py). Administrators only."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import ldap_auth
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/ldap", tags=["auth"])


class LdapSettings(BaseModel):
    enabled: bool = False
    url: str
    starttls: bool = False
    verify_tls: bool = True
    ca_cert: str = ""
    bind_dn: str = ""
    bind_password: str | None = None  # write-only; empty keeps the stored one
    base_dn: str
    user_filter: str = "(&(objectClass=person)(|(uid={username})(sAMAccountName={username})))"
    group_attribute: str = "memberOf"
    admin_groups: str = ""
    allowed_groups: str = ""


class LdapTest(BaseModel):
    settings: LdapSettings | None = None
    username: str | None = None
    password: str | None = None


@router.get("/config")
def get_ldap_config(user: dict = Depends(require_role("admin"))):
    return ldap_auth.public_config()


@router.put("/config")
def put_ldap_config(payload: LdapSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = ldap_auth.set_config(payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "ldap_config", payload.url, "succes", "enabled" if payload.enabled else "disabled")
    return result


@router.post("/test")
def test_ldap(payload: LdapTest, user: dict = Depends(require_role("admin"))):
    """Bind with the service account and read the search base; with a user name and password, also say whether the
    directory accepts them and which role they would get. Nothing is saved and no account is created."""
    try:
        return ldap_auth.test(
            payload.settings.model_dump() if payload.settings else None, payload.username, payload.password
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ldap_auth.LdapError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
