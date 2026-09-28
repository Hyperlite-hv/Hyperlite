import re
import sqlite3
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt import PyJWTError
from pydantic import BaseModel

from app.core import login_guard
from app.core.api_tokens import create_token, list_tokens, revoke_all_tokens, revoke_token
from app.core.audit import log_action
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
    session_remembered,
    set_password,
    verify_password,
)
from app.core.twofa import generate_secret, provisioning_uri, qr_code_svg, verify_code

router = APIRouter(prefix="/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")

# --- Brute-force protection on every check of a password or a 2FA code (sign-in, 2FA step, password
# change, 2FA removal). Failures are stored in the database (app/core/login_guard.py), so a service
# restart does not reset the lock.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 300  # sliding window over which failures count

# Rate limiting PER IP. The per-ACCOUNT lock above does not protect against an
# attacker who tries many different USERNAMES from the same source (the account
# lock never triggers if each account is tried only once or twice). A wider
# per-IP lock (more attempts tolerated, since one IP can legitimately carry
# several users behind a NAT or proxy) covers that distinct case. Same storage.
LOGIN_IP_MAX_ATTEMPTS = 20
LOGIN_IP_WINDOW_S = 300


def _login_locked_out(username):
    return login_guard.recent_failures("user", username, LOGIN_WINDOW_S) >= LOGIN_MAX_ATTEMPTS


def _login_record_failure(username):
    login_guard.record_failure("user", username)


def _login_clear_failures(username):
    login_guard.clear_failures("user", username)


def _client_ip(request: Request):
    return request.client.host if request.client else "unknown"


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


class TokenCreate(BaseModel):
    name: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    code: str | None = None  # TOTP code, required when the account has 2FA


def _session_response(token, user):
    """password_change_required: the dashboard shows the password change screen and nothing else."""
    return {
        "access_token": token,
        "token_type": "bearer",
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
    if _login_locked_out(form_data.username):
        log_action(form_data.username, "login", "auth", "echec", f"Locked out ({LOGIN_MAX_ATTEMPTS} recent failures)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts for this account, try again in {LOGIN_WINDOW_S // 60} minutes",
        )
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        _login_record_failure(form_data.username)
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

    # Correct password but 2FA enabled: no full session token yet, only a 5-minute
    # intermediate token (see create_preauth_token) that the frontend exchanges for
    # the real token through /auth/login/2fa after the TOTP code. The account's
    # failures are cleared only once the code is right too: clearing them here would
    # let whoever knows the password guess the code without limit.
    if user["totp_enabled"]:
        pre_auth = create_preauth_token(user["username"], remember)
        log_action(user["username"], "login", "auth", "succes", "Password validated, 2FA code required")
        return {"require_2fa": True, "pre_auth_token": pre_auth}

    _login_clear_failures(user["username"])
    token = create_access_token({"sub": user["username"], "role": user["role"]}, remember=remember)
    _record_login(user["username"])
    log_action(user["username"], "login", "auth", "succes")
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

    if _login_locked_out(username):
        log_action(username, "login", "auth", "echec", f"Locked out ({LOGIN_MAX_ATTEMPTS} recent failures)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts for this account, try again in {LOGIN_WINDOW_S // 60} minutes",
        )

    user = get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not verify_code(user["totp_secret"], payload.code):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "login", "auth", "echec", "Invalid 2FA code")
        raise HTTPException(status_code=401, detail="Invalid code")

    _login_clear_failures(username)
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
def setup_2fa(user: dict = Depends(get_session_user)):
    if user["totp_enabled"]:
        raise HTTPException(status_code=400, detail="2FA is already enabled: disable it before generating a new one")
    secret = generate_secret()
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ? WHERE username = ?", (secret, user["username"]))
        conn.commit()
    uri = provisioning_uri(secret, user["username"])
    return {"secret": secret, "otpauth_uri": uri, "qr_code_svg": qr_code_svg(uri)}


@router.post("/2fa/confirm")
def confirm_2fa(payload: TwoFAConfirm, user: dict = Depends(get_session_user)):
    fresh = get_user(user["username"])
    if not fresh["totp_secret"]:
        raise HTTPException(status_code=400, detail="No pending 2FA setup: run /auth/2fa/setup first")
    if not verify_code(fresh["totp_secret"], payload.code):
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
    ip = _client_ip(request)
    if _login_ip_locked_out(ip) or _login_locked_out(username):
        log_action(username, "disable_2fa", username, "echec", "Locked out after repeated failures")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts, try again in {LOGIN_WINDOW_S // 60} minutes",
        )
    # 400, not 401, for a wrong password or code: the session itself is valid (a 401 signs the dashboard out).
    if not verify_password(payload.password, user["hashed_password"]):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "disable_2fa", username, "echec", "Incorrect password")
        raise HTTPException(status_code=400, detail="Incorrect password")
    if user["totp_enabled"] and not verify_code(user["totp_secret"], payload.code or ""):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "disable_2fa", username, "echec", "Invalid 2FA code")
        raise HTTPException(status_code=400, detail="Invalid 2FA code")
    _login_clear_failures(username)
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?", (user["username"],))
        conn.commit()
    log_action(user["username"], "disable_2fa", user["username"], "succes")
    return {"message": "2FA disabled"}


# --- Self-service API tokens: designed for automation (scripts, Terraform, cron),
# an authentication alternative to the session JWT (see
# security.py::get_current_user, which falls back to api_tokens.verify_token when
# the token is not a valid JWT).
@router.get("/tokens")
def get_api_tokens(user: dict = Depends(get_current_user)):
    return list_tokens(user["username"])


@router.post("/tokens", status_code=201)
def post_api_token(payload: TokenCreate, user: dict = Depends(get_current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="The token name is required")
    token_id, token = create_token(user["username"], name)
    log_action(user["username"], "create_api_token", name, "succes")
    # The plain token is returned only HERE, once: it can never be retrieved again
    # afterwards (only its SHA-256 hash is stored).
    return {"id": token_id, "name": name, "token": token}


@router.delete("/tokens/{token_id}")
def delete_api_token(token_id: int, user: dict = Depends(get_current_user)):
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
    ip = _client_ip(request)
    if _login_ip_locked_out(ip) or _login_locked_out(username):
        log_action(username, "change_password", username, "echec", "Locked out after repeated failures")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts, try again in {LOGIN_WINDOW_S // 60} minutes",
        )
    if user.get("auth_source") == "sso":
        raise HTTPException(status_code=400, detail="SSO account: the password is managed by the identity provider")
    # 400, not 401, for a wrong password or code: the session itself is valid (a 401 signs the dashboard out).
    if not verify_password(payload.current_password, user["hashed_password"]):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "change_password", username, "echec", "Incorrect current password")
        raise HTTPException(status_code=400, detail="Incorrect current password")
    if user["totp_enabled"] and not verify_code(user["totp_secret"], payload.code or ""):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "change_password", username, "echec", "Invalid 2FA code")
        raise HTTPException(status_code=400, detail="Invalid 2FA code")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="The new password must be different from the current one")
    problem = password_problem(payload.new_password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    with get_conn() as conn:
        set_password(conn, username, payload.new_password)
        conn.commit()
    _login_clear_failures(username)
    log_action(username, "change_password", username, "succes", "Other sessions signed out")
    fresh = create_access_token({"sub": username, "role": user["role"]}, remember=session_remembered(token))
    return {"access_token": fresh, "token_type": "bearer", "role": user["role"]}


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
    for group_id in get_user_groups(username):
        remove_group_member(group_id, username)
    log_action(user["username"], "delete_user", username, "succes")
    return {"message": "User deleted"}
