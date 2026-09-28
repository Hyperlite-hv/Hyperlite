import os
import time
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError

from app.core.database import get_conn
from app.core.passwords import bcrypt_hash, bcrypt_verify

SECRET_KEY = os.environ.get("HYPERLITE_SECRET_KEY", "dev-" + os.urandom(16).hex())
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 240  # 4 h: a long working or testing session used to expire the token silently (60 min), e.g. an ISO upload failing at the final step with no clear message

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def verify_password(plain, hashed):
    return bcrypt_verify(plain, hashed)


def hash_password(plain):
    return bcrypt_hash(plain)


REMEMBER_TOKEN_EXPIRE_DAYS = 7  # "Stay signed in" on the login screen


def create_access_token(data: dict, remember: bool = False):
    to_encode = data.copy()
    lifetime = (
        timedelta(days=REMEMBER_TOKEN_EXPIRE_DAYS) if remember else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    now = datetime.now(UTC)
    # iat (issued at, whole seconds): lets a password change sign out every session opened before it
    # (see _session_user and set_password).
    to_encode.update({"exp": now + lifetime, "iat": int(now.timestamp())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def set_password(conn, username: str, plain: str):
    """Store a new password for the account and sign out its existing sessions: every session token issued
    before this second is refused from now on. The caller commits."""
    conn.execute(
        "UPDATE users SET hashed_password = ?, password_changed_at = ? WHERE username = ?",
        (hash_password(plain), int(time.time()), username),
    )


def create_preauth_token(username: str, remember: bool = False):
    """Intermediate token issued after a correct password but BEFORE the TOTP
    code is verified. It only proves "this password is right", not "this user
    is authenticated". Short-lived (5 min, enough time to type a code) and
    explicitly marked `2fa_pending`: get_current_user() rejects that claim so
    that a stolen or intercepted intermediate token can never serve as a full
    session token."""
    to_encode = {"sub": username, "2fa_pending": True, "remember": bool(remember)}
    expire = datetime.now(UTC) + timedelta(minutes=5)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user(username: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def authenticate_user(username: str, password: str):
    user = get_user(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user


def _credentials_exception():
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_session(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except PyJWTError:
        return None


def _session_user(payload: dict):
    username = payload.get("sub")
    # 2fa_pending: intermediate token (see create_preauth_token). It proves the
    # password but not the second factor, and must never be accepted as a normal
    # session token.
    if username is None or payload.get("2fa_pending"):
        raise _credentials_exception()
    user = get_user(username)
    if user is None:
        raise _credentials_exception()
    # A password change or reset signs out every session opened before it (set_password). A token without
    # iat predates that mechanism and is refused as soon as the password has been changed once.
    changed = user.get("password_changed_at")
    if changed and int(payload.get("iat") or 0) < changed:
        raise _credentials_exception()
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = _decode_session(token)
    if payload is not None:
        return _session_user(payload)

    # Not a valid JWT: it may be an API token instead of a session token. Same
    # Authorization: Bearer header, different format ("hlt_" prefix), so no new
    # FastAPI dependency has to be wired everywhere, just a fallback here.
    from app.core.api_tokens import (
        verify_token,  # late import: avoids a cycle (api_tokens -> database, no way back to security)
    )

    user = verify_token(token)
    if user is None:
        raise _credentials_exception()
    return user


async def get_session_user(token: str = Depends(oauth2_scheme)):
    """Like get_current_user, but only for a signed-in person (a session token), never an API token: an API
    token that leaked from a script must not be able to take the account over (e.g. by changing its
    password)."""
    payload = _decode_session(token)
    if payload is None:
        from app.core.api_tokens import verify_token  # late import: see get_current_user

        if verify_token(token) is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not allowed with an API token: sign in to the dashboard",
            )
        raise _credentials_exception()
    return _session_user(payload)


def require_role(*roles):
    async def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' is not allowed to perform this action",
            )
        return user

    return checker


def require_vm_privilege(privilege):
    """Like require_role, but checks a privilege scoped to the target VM (see
    app/core/permissions.py) instead of a global role: an admin always passes,
    an observer keeps global vm.view access, and a user or group with an ACL on
    that VM (or on a pool containing it) gets the privileges of the scoped
    role. The path parameter must be named `name` (as on every
    /vms/{name}/... route)."""
    from app.core.permissions import has_privilege  # late import: avoids a cycle with permissions.py

    async def checker(name: str, user: dict = Depends(get_current_user)):
        if not has_privilege(user, name, privilege):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient rights on VM '{name}' (required privilege: {privilege})",
            )
        return user

    return checker


def require_container_privilege(privilege):
    """Equivalent of require_vm_privilege() for LXC containers; see
    app/core/permissions.py::has_container_privilege."""
    from app.core.permissions import has_container_privilege  # import tardif : evite un cycle

    async def checker(name: str, user: dict = Depends(get_current_user)):
        if not has_container_privilege(user, name, privilege):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient rights on container '{name}' (required privilege: {privilege})",
            )
        return user

    return checker
