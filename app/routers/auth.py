import re
import sqlite3
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt import PyJWTError
from pydantic import BaseModel, Field

from app.core import login_guard, webauthn_keys
from app.core.api_tokens import create_token, list_tokens, revoke_all_tokens, revoke_token
from app.core.audit import log_action
from app.core.client_address import client_address
from app.core.database import get_conn
from app.core.password_policy import password_problem
from app.core.permissions import get_user_groups, remove_group_member
from app.core.security import (
    ALGORITHM,
    SECRET_KEY,
    authenticate_user,
    create_access_token,
    create_preauth_token,
    flag_weak_password,
    get_current_user,
    get_current_user_pending_ok,
    get_session_user,
    get_session_user_pending_ok,
    get_user,
    hash_password,
    oauth2_scheme,
    require_role,
    revoke_session,
    session_remembered,
    set_password,
    verify_password,
)
from app.core.twofa import generate_secret, provisioning_uri, qr_code_svg, seal_secret, verify_code

router = APIRouter(prefix="/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")

# --- Brute-force protection on every check of a password or a 2FA code (sign-in, 2FA step, password
# change, 2FA removal). Failures are stored in the database (app/core/login_guard.py), so a service
# restart does not reset the lock.
#
# The sign-in lock is kept per (account, source address): with a lock per account alone, anyone could keep the
# owner out with a wrong password every minute. A much higher ceiling per account still stops a guessing attack
# spread over many addresses. Actions of an already signed-in user (password change, 2FA removal...) count on
# their own: failed sign-ins by someone else must not stop the owner from securing the account.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 300  # sliding window over which failures count
LOGIN_ACCOUNT_MAX_ATTEMPTS = 50

# Rate limiting PER IP. The per-ACCOUNT lock above does not protect against an
# attacker who tries many different USERNAMES from the same source (the account
# lock never triggers if each account is tried only once or twice). A wider
# per-IP lock (more attempts tolerated, since one IP can legitimately carry
# several users behind a NAT or proxy) covers that distinct case. Same storage.
LOGIN_IP_MAX_ATTEMPTS = 20
LOGIN_IP_WINDOW_S = 300


def _login_locked_out(username, ip):
    return (
        login_guard.recent_failures("userip", f"{username}|{ip}", LOGIN_WINDOW_S) >= LOGIN_MAX_ATTEMPTS
        or login_guard.recent_failures("user", username, LOGIN_WINDOW_S) >= LOGIN_ACCOUNT_MAX_ATTEMPTS
    )


def _login_record_failure(username, ip):
    login_guard.record_failure("userip", f"{username}|{ip}")
    login_guard.record_failure("user", username)


def _login_clear_failures(username, ip):
    login_guard.clear_failures("userip", f"{username}|{ip}")


def _reauth_locked_out(username):
    return login_guard.recent_failures("reauth", username, LOGIN_WINDOW_S) >= LOGIN_MAX_ATTEMPTS


def _reauth_record_failure(username):
    login_guard.record_failure("reauth", username)


def _reauth_clear_failures(username):
    login_guard.clear_failures("reauth", username)


def _refuse_reauth_if_locked(username, action):
    if _reauth_locked_out(username):
        log_action(username, action, username, "echec", "Locked out after repeated failures")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts, try again in {LOGIN_WINDOW_S // 60} minutes",
        )


def _check_current_password(user, password, action):
    """The account's password, asked again before a change that weakens or takes over the account. SSO accounts
    have no usable local password: their sign-in is the identity provider's. 400, not 401, on a wrong password:
    the session itself is valid (a 401 signs the dashboard out)."""
    if user.get("auth_source") == "sso":
        return
    username = user["username"]
    _refuse_reauth_if_locked(username, action)
    if not verify_password(password or "", user["hashed_password"]):
        _reauth_record_failure(username)
        log_action(username, action, username, "echec", "Incorrect password")
        raise HTTPException(status_code=400, detail="Incorrect password")


def _client_ip(request: Request):
    return client_address(request)


def _login_ip_locked_out(ip):
    return login_guard.recent_failures("ip", ip, LOGIN_IP_WINDOW_S) >= LOGIN_IP_MAX_ATTEMPTS


def _login_ip_record_failure(ip):
    login_guard.record_failure("ip", ip)


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "observateur"


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None


class Login2FA(BaseModel):
    pre_auth_token: str
    code: str


class TwoFAConfirm(BaseModel):
    code: str


class TwoFADisable(BaseModel):
    password: str
    code: str | None = None  # current TOTP code, required when 2FA is on


class LoginWebAuthnOptions(BaseModel):
    pre_auth_token: str


class LoginWebAuthn(BaseModel):
    pre_auth_token: str
    credential: dict


class WebAuthnRegister(BaseModel):
    credential: dict
    name: str = Field("", max_length=64)


class WebAuthnDelete(BaseModel):
    password: str


class TokenCreate(BaseModel):
    name: str
    # A token made by hand used to never expire: a leaked script token stayed valid for good. 90 days unless
    # asked otherwise; null keeps a token without expiry, when explicitly chosen.
    expires_days: int | None = Field(90, ge=1, le=3650)


class PasswordConfirm(BaseModel):
    password: str = ""


class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    code: str | None = None  # TOTP code, required when the account has 2FA


def _session_response(token, user):
    """password_change_required: the dashboard shows the password change screen and nothing else."""
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
        "password_change_required": bool(user.get("must_change_password")),
    }


def _record_login(username):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (datetime.now(UTC).isoformat(), username))
        conn.commit()


@router.post("/login")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), remember: bool = Form(False)):
    ip = _client_ip(request)
    if _login_ip_locked_out(ip):
        log_action(
            form_data.username,
            "login",
            "auth",
            "echec",
            f"IP locked out ({LOGIN_IP_MAX_ATTEMPTS} recent failures from {ip})",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts from this address, try again in {LOGIN_IP_WINDOW_S // 60} minutes",
        )
    if _login_locked_out(form_data.username, ip):
        log_action(form_data.username, "login", "auth", "echec", f"Locked out ({LOGIN_MAX_ATTEMPTS} recent failures)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts for this account, try again in {LOGIN_WINDOW_S // 60} minutes",
        )
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        _login_record_failure(form_data.username, ip)
        _login_ip_record_failure(ip)
        log_action(form_data.username, "login", "auth", "echec", "Invalid credentials")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    # The only moment the plain password is known: one that no longer meets the policy (set before the policy
    # existed) must be changed before anything else. Checked before the 2FA step so abandoning it changes nothing.
    if user.get("auth_source", "local") == "local" and password_problem(form_data.password, user["username"]):
        if not user.get("must_change_password"):
            flag_weak_password(user["username"])
            log_action(user["username"], "login", "auth", "succes", "Weak password: a change is required")
        user["must_change_password"] = 1

    # Correct password but a second factor (TOTP code or security key) set up: no full session token yet, only a 5-minute
    # intermediate token (see create_preauth_token) that the frontend exchanges for
    # the real token through /auth/login/2fa after the TOTP code. The account's
    # failures are cleared only once the code is right too: clearing them here would
    # let whoever knows the password guess the code without limit.
    methods = [
        m for m, on in (("totp", user["totp_enabled"]), ("webauthn", webauthn_keys.has_keys(user["username"]))) if on
    ]
    if methods:
        pre_auth = create_preauth_token(user["username"], remember)
        log_action(user["username"], "login", "auth", "succes", "Password validated, second factor required")
        return {"require_2fa": True, "pre_auth_token": pre_auth, "methods": methods}

    _login_clear_failures(user["username"], ip)
    token = create_access_token({"sub": user["username"], "role": user["role"]}, remember=remember)
    _record_login(user["username"])
    log_action(user["username"], "login", "auth", "succes")
    return _session_response(token, user)


def _preauth_username(token):
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Login session expired, sign in again") from None
    username = claims.get("sub")
    if not claims.get("2fa_pending") or not username:
        raise HTTPException(status_code=401, detail="Invalid pre-authentication token")
    return username, claims


def _refuse_if_locked(request, username, action):
    ip = _client_ip(request)
    if _login_ip_locked_out(ip) or (username and _login_locked_out(username, ip)):
        log_action(username or "system", action, "auth", "echec", "Locked out after repeated failures")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts, try again in {LOGIN_WINDOW_S // 60} minutes",
        )
    return ip


@router.post("/login/webauthn/options")
def login_webauthn_options(request: Request, payload: LoginWebAuthnOptions):
    """Second step with a security key: the challenge for this sign-in, after a valid password."""
    username, _claims = _preauth_username(payload.pre_auth_token)
    _refuse_if_locked(request, username, "login")
    try:
        return webauthn_keys.authentication_options(username, request.headers.get("origin"))
    except webauthn_keys.WebAuthnError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None


@router.post("/login/webauthn")
def login_webauthn(request: Request, payload: LoginWebAuthn):
    username, claims = _preauth_username(payload.pre_auth_token)
    ip = _refuse_if_locked(request, username, "login")
    user = get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    try:
        ok = webauthn_keys.authenticate(username, request.headers.get("origin"), payload.credential)
    except webauthn_keys.WebAuthnError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None
    if not ok:
        _login_record_failure(username, ip)
        _login_ip_record_failure(ip)
        log_action(username, "login", "auth", "echec", "Security key refused")
        raise HTTPException(status_code=401, detail="Security key refused: try again, or use the code")
    _login_clear_failures(username, ip)
    token = create_access_token({"sub": user["username"], "role": user["role"]}, remember=bool(claims.get("remember")))
    _record_login(username)
    log_action(username, "login", "auth", "succes", "Security key validated")
    return _session_response(token, user)


@router.post("/login/2fa")
def login_2fa(request: Request, payload: Login2FA):
    """Second step of the login when /auth/login returned require_2fa. It reuses the
    same brute-force lock as /auth/login (per username AND per IP): a TOTP code
    has 6 digits (1M combinations), too few to let it be guessed without a
    limit."""
    ip = _client_ip(request)
    if _login_ip_locked_out(ip):
        log_action(
            "system", "login", "auth", "echec", f"IP locked out ({LOGIN_IP_MAX_ATTEMPTS} recent failures from {ip})"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts from this address, try again in {LOGIN_IP_WINDOW_S // 60} minutes",
        )
    try:
        claims = jwt.decode(payload.pre_auth_token, SECRET_KEY, algorithms=[ALGORITHM])
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Login session expired, sign in again") from None
    username = claims.get("sub")
    if not claims.get("2fa_pending") or not username:
        raise HTTPException(status_code=401, detail="Invalid pre-authentication token")

    if _login_locked_out(username, ip):
        log_action(username, "login", "auth", "echec", f"Locked out ({LOGIN_MAX_ATTEMPTS} recent failures)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts for this account, try again in {LOGIN_WINDOW_S // 60} minutes",
        )

    user = get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not verify_code(username, user["totp_secret"], payload.code):
        _login_record_failure(username, ip)
        _login_ip_record_failure(ip)
        log_action(username, "login", "auth", "echec", "Invalid 2FA code")
        raise HTTPException(status_code=401, detail="Invalid code (a code is accepted once: wait for the next one)")

    _login_clear_failures(username, ip)
    token = create_access_token({"sub": user["username"], "role": user["role"]}, remember=bool(claims.get("remember")))
    _record_login(username)
    log_action(username, "login", "auth", "succes", "2FA validated")
    return _session_response(token, user)


@router.get("/me")
def me(user: dict = Depends(get_current_user_pending_ok)):
    return {
        "username": user["username"],
        "role": user["role"],
        "totp_enabled": bool(user["totp_enabled"]),
        "cles_securite": len(webauthn_keys.list_keys(user["username"])),
        "auth_source": user.get("auth_source", "local"),
        "password_change_required": bool(user.get("must_change_password")),
    }


# --- Self-service 2FA: each user manages their own 2FA, no need to be an admin
# (require_role). Two-step flow: /2fa/setup generates a secret and ALREADY stores
# it in the database, but totp_enabled stays 0. Until /2fa/confirm has verified a
# real code, 2FA is NOT active, so a secret that was generated but never
# confirmed (e.g. the user closes the tab while scanning the QR code) blocks no
# one at the next login.
@router.post("/2fa/setup")
def setup_2fa(payload: PasswordConfirm, user: dict = Depends(get_session_user)):
    """Enrolling a second factor takes the password, like removing one: with a stolen session alone, enrolling
    the attacker's own factor locked the owner out of the account for good."""
    if user["totp_enabled"]:
        raise HTTPException(status_code=400, detail="2FA is already enabled: disable it before generating a new one")
    _check_current_password(user, payload.password, "enable_2fa")
    _reauth_clear_failures(user["username"])
    secret = generate_secret()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_last_step = NULL WHERE username = ?",
            (seal_secret(secret), user["username"]),
        )
        conn.commit()
    uri = provisioning_uri(secret, user["username"])
    return {"secret": secret, "otpauth_uri": uri, "qr_code_svg": qr_code_svg(uri)}


@router.post("/2fa/confirm")
def confirm_2fa(payload: TwoFAConfirm, user: dict = Depends(get_session_user)):
    fresh = get_user(user["username"])
    if not fresh["totp_secret"]:
        raise HTTPException(status_code=400, detail="No pending 2FA setup: run /auth/2fa/setup first")
    if not verify_code(user["username"], fresh["totp_secret"], payload.code):
        # 400, not 401: the session itself is valid, only the submitted code is wrong (a 401 makes
        # the dashboard treat the session as expired and sign the user out).
        raise HTTPException(status_code=400, detail="Invalid code")
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_enabled = 1 WHERE username = ?", (user["username"],))
        conn.commit()
    log_action(user["username"], "enable_2fa", user["username"], "succes")
    return {"message": "2FA enabled"}


@router.post("/2fa/disable")
def disable_2fa(request: Request, payload: TwoFADisable, user: dict = Depends(get_session_user)):
    """Removing 2FA takes the password AND a current code, from a signed-in session (never an API token):
    a session left open, or a leaked password, is not enough to strip the second factor. Wrong attempts
    count towards the same lock as the sign-in form."""
    username = user["username"]
    _refuse_reauth_if_locked(username, "disable_2fa")
    # 400, not 401, for a wrong password or code: the session itself is valid (a 401 signs the dashboard out).
    if not verify_password(payload.password, user["hashed_password"]):
        _reauth_record_failure(username)
        log_action(username, "disable_2fa", username, "echec", "Incorrect password")
        raise HTTPException(status_code=400, detail="Incorrect password")
    if user["totp_enabled"] and not verify_code(username, user["totp_secret"], payload.code or ""):
        _reauth_record_failure(username)
        log_action(username, "disable_2fa", username, "echec", "Invalid 2FA code")
        raise HTTPException(status_code=400, detail="Invalid 2FA code (a code is accepted once: wait for the next one)")
    _reauth_clear_failures(username)
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?", (user["username"],))
        conn.commit()
    log_action(user["username"], "disable_2fa", user["username"], "succes")
    return {"message": "2FA disabled"}


# --- Security keys (WebAuthn): each user manages their own, from a signed-in session (never an API token).


@router.get("/webauthn/keys")
def list_webauthn_keys(user: dict = Depends(get_session_user)):
    return webauthn_keys.list_keys(user["username"])


@router.post("/webauthn/keys/options")
def webauthn_register_options(request: Request, payload: PasswordConfirm, user: dict = Depends(get_session_user)):
    """Adding a key starts here and takes the password, like removing one (see setup_2fa): the registration that
    follows needs the challenge issued here."""
    _check_current_password(user, payload.password, "add_security_key")
    _reauth_clear_failures(user["username"])
    try:
        return webauthn_keys.registration_options(user["username"], request.headers.get("origin"))
    except webauthn_keys.WebAuthnError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None


@router.post("/webauthn/keys", status_code=201)
def webauthn_register(request: Request, payload: WebAuthnRegister, user: dict = Depends(get_session_user)):
    try:
        key_id = webauthn_keys.register(
            user["username"], request.headers.get("origin"), payload.credential, payload.name
        )
    except webauthn_keys.WebAuthnError as e:
        log_action(user["username"], "add_security_key", user["username"], "echec", e.message)
        raise HTTPException(status_code=e.status, detail=e.message) from None
    log_action(user["username"], "add_security_key", user["username"], "succes", payload.name or "Security key")
    return {"id": key_id, "cles": webauthn_keys.list_keys(user["username"])}


@router.delete("/webauthn/keys/{key_id}")
def webauthn_delete(request: Request, key_id: int, payload: WebAuthnDelete, user: dict = Depends(get_session_user)):
    """Removing a key weakens the account: it takes the password, with the same lock as the sign-in form."""
    username = user["username"]
    _check_current_password(user, payload.password, "remove_security_key")
    if not webauthn_keys.delete_key(username, key_id):
        raise HTTPException(status_code=404, detail="Security key not found")
    _reauth_clear_failures(username)
    log_action(username, "remove_security_key", username, "succes", f"key #{key_id}")
    return {"cles": webauthn_keys.list_keys(username)}


# --- Self-service API tokens: designed for automation (scripts, Terraform, cron),
# an authentication alternative to the session JWT (see
# security.py::get_current_user, which falls back to api_tokens.verify_token when
# the token is not a valid JWT).
@router.get("/tokens")
def get_api_tokens(user: dict = Depends(get_current_user)):
    return list_tokens(user["username"])


@router.post("/tokens", status_code=201)
def post_api_token(payload: TokenCreate, user: dict = Depends(get_session_user)):
    """From a signed-in session only: a leaked API token must not be able to mint others, which revoking it would
    not remove."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="The token name is required")
    expires_at = (
        (datetime.now(UTC) + timedelta(days=payload.expires_days)).isoformat() if payload.expires_days else None
    )
    token_id, token = create_token(user["username"], name, expires_at=expires_at)
    log_action(
        user["username"],
        "create_api_token",
        name,
        "succes",
        f"expires {expires_at[:10]}" if expires_at else "no expiry",
    )
    # The plain token is returned only HERE, once: it can never be retrieved again
    # afterwards (only its SHA-256 hash is stored).
    return {"id": token_id, "name": name, "token": token, "expires_at": expires_at}


@router.delete("/tokens/{token_id}")
def delete_api_token(token_id: int, user: dict = Depends(get_current_user)):
    """A session revokes any token of its account; an API token only itself (a workstation signing out)."""
    if user.get("api_token_id") is not None and user["api_token_id"] != token_id:
        raise HTTPException(status_code=403, detail="An API token can only revoke itself: sign in to the dashboard")
    if not revoke_token(user["username"], token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    log_action(user["username"], "revoke_api_token", str(token_id), "succes")
    return {"message": "Token revoked"}


@router.post("/me/password")
def change_my_password(
    request: Request,
    payload: PasswordChange,
    user: dict = Depends(get_session_user_pending_ok),
    token: str = Depends(oauth2_scheme),
):
    """Any signed-in user changes their own password. It takes the current password (and the 2FA code when
    2FA is on), so a session left open is not enough to take the account over; wrong attempts count towards
    the same lock as the sign-in form. Every other session of the account is signed out; the one making the
    change gets a fresh token, with the same lifetime ("Stay signed in" is kept). Never with an API token
    (get_session_user)."""
    username = user["username"]
    _refuse_reauth_if_locked(username, "change_password")
    if user.get("auth_source") == "sso":
        raise HTTPException(status_code=400, detail="SSO account: the password is managed by the identity provider")
    # 400, not 401, for a wrong password or code: the session itself is valid (a 401 signs the dashboard out).
    if not verify_password(payload.current_password, user["hashed_password"]):
        _reauth_record_failure(username)
        log_action(username, "change_password", username, "echec", "Incorrect current password")
        raise HTTPException(status_code=400, detail="Incorrect current password")
    if user["totp_enabled"] and not verify_code(username, user["totp_secret"], payload.code or ""):
        _reauth_record_failure(username)
        log_action(username, "change_password", username, "echec", "Invalid 2FA code")
        raise HTTPException(status_code=400, detail="Invalid 2FA code (a code is accepted once: wait for the next one)")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="The new password must be different from the current one")
    problem = password_problem(payload.new_password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    with get_conn() as conn:
        set_password(conn, username, payload.new_password)
        conn.commit()
    _reauth_clear_failures(username)
    # A password is changed when the account may be compromised: its API and workstation tokens go too, as on a
    # reset by an administrator. Otherwise a stolen script token kept full, permanent access.
    revoked = revoke_all_tokens(username)
    log_action(
        username, "change_password", username, "succes", f"Other sessions signed out, {revoked} token(s) revoked"
    )
    fresh = create_access_token({"sub": username, "role": user["role"]}, remember=session_remembered(token))
    return {"access_token": fresh, "token_type": "bearer", "role": user["role"], "revoked_tokens": revoked}


@router.post("/logout")
def logout(token: str = Depends(oauth2_scheme), user: dict = Depends(get_current_user_pending_ok)):
    """Sign this session out on the server: its token is refused from now on, in every tab and window that holds
    it. An API token is not a session: revoke it from the tokens list."""
    if user.get("api_token_id") is not None:
        raise HTTPException(status_code=400, detail="An API token is revoked from the tokens list, not signed out")
    revoke_session(token)
    log_action(user["username"], "logout", "auth", "succes")
    return {"message": "Signed out"}


@router.get("/users")
def list_users(user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT username, role, auth_source, totp_enabled, last_login_at FROM users ORDER BY username"
        ).fetchall()
    return [{**dict(r), "totp_enabled": bool(r["totp_enabled"])} for r in rows]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, user: dict = Depends(require_role("admin"))):
    if not USERNAME_RE.match(payload.username):
        raise HTTPException(status_code=422, detail="Invalid username (2-32 characters: letters, digits, . _ -)")
    problem = password_problem(payload.password, payload.username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if payload.role not in ("admin", "observateur"):
        raise HTTPException(status_code=422, detail="Invalid role (admin or observateur)")
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                (payload.username, hash_password(payload.password), payload.role),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=422, detail=f"User '{payload.username}' already exists") from None
    log_action(user["username"], "create_user", payload.username, "succes")
    return {"username": payload.username, "role": payload.role}


@router.patch("/users/{username}")
def update_user(username: str, payload: UserUpdate, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT username, role, auth_source FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        # SSO: the local password of an SSO account is a random secret that is never
        # disclosed (see sso.py::provision_user). "Changing" it here would give the
        # misleading impression that a local login would then work, whereas the role
        # itself is overwritten at the next SSO login anyway. The role remains editable
        # by hand (useful as a fallback when the IdP is down).
        if payload.password is not None and existing["auth_source"] == "sso":
            raise HTTPException(status_code=400, detail="SSO account: the password cannot be changed locally")
        if payload.password is not None:
            # An administrator changes their own password through /auth/me/password, which asks for the
            # current one (and the 2FA code): a stolen admin session must not be enough to lock the owner out.
            if username == user["username"]:
                raise HTTPException(status_code=400, detail="Use 'Change my password' to change your own password")
            problem = password_problem(payload.password, username)
            if problem:
                raise HTTPException(status_code=422, detail=problem)
        if payload.role is not None:
            if payload.role not in ("admin", "observateur"):
                raise HTTPException(status_code=422, detail="Invalid role (admin or observateur)")
            if existing["role"] == "admin" and payload.role != "admin" and username == user["username"]:
                raise HTTPException(status_code=400, detail="You cannot remove your own admin rights")
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (payload.role, username))
        if payload.password is not None:
            set_password(conn, username, payload.password)
        conn.commit()
    result = {"message": "User updated"}
    if payload.password is not None:
        # A reset means the account may be in the wrong hands: its sessions are already signed out
        # (set_password), its API and workstation tokens go too.
        revoked = revoke_all_tokens(username)
        log_action(
            user["username"], "reset_password", username, "succes", f"Sessions signed out, {revoked} token(s) revoked"
        )
        result["revoked_tokens"] = revoked
    if payload.role is not None:
        log_action(user["username"], "update_user", username, "succes")
    return result


@router.delete("/users/{username}")
def delete_user(username: str, user: dict = Depends(require_role("admin"))):
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        if row["role"] == "admin":
            remaining_admins = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND username != ?", (username,)
            ).fetchone()["n"]
            if remaining_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot delete the last admin account")
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    webauthn_keys.delete_all_keys(username)
    for group_id in get_user_groups(username):
        remove_group_member(group_id, username)
    log_action(user["username"], "delete_user", username, "succes")
    return {"message": "User deleted"}
