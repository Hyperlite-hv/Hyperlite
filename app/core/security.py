import logging
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError

from app.core.database import get_conn
from app.core.passwords import bcrypt_hash, bcrypt_verify

logger = logging.getLogger(__name__)

MIN_SECRET_KEY_LENGTH = 32


def _load_secret_key():
    """The key that signs every session token. Set but empty or short, it is refused: an empty HMAC key lets anyone
    forge a session. Unset (development only), a random key is used and every session is lost at restart; the
    installer always writes one to .env."""
    raw = os.environ.get("HYPERLITE_SECRET_KEY")
    if raw is None:
        logger.warning("HYPERLITE_SECRET_KEY is not set: using a random key, every session ends at restart")
        return "dev-" + secrets.token_hex(32)
    if len(raw.strip()) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            f"HYPERLITE_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters "
            "(generate one with: openssl rand -hex 32)"
        )
    return raw


SECRET_KEY = _load_secret_key()
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
    # (see _session_user and set_password). jti: identifies this session, so that signing out revokes it.
    to_encode.update({"exp": now + lifetime, "iat": int(now.timestamp()), "jti": secrets.token_urlsafe(16)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def revoke_session(token: str) -> bool:
    """Sign a session token out before it expires (POST /auth/logout). A JWT is valid on its own until its expiry:
    without this, "Sign out" only forgot it in the browser, and a copy (another tab, a stolen token) kept working."""
    payload = _decode_session(token)
    if not payload or not payload.get("jti"):
        return False
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT OR IGNORE INTO revoked_sessions (jti, expires_at) VALUES (?, ?)",
            (payload["jti"], int(payload.get("exp") or now)),
        )
        conn.commit()
    return True


def _session_revoked(jti) -> bool:
    if not jti:
        return False
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM revoked_sessions WHERE jti = ?", (jti,)).fetchone() is not None


def session_remembered(token: str) -> bool:
    """True when this session token was issued with "Stay signed in" (it lives longer than a normal one), so a
    token that replaces it (after a password change) keeps the same lifetime."""
    payload = _decode_session(token) or {}
    exp, iat = payload.get("exp"), payload.get("iat")
    return bool(exp and iat) and exp - iat > ACCESS_TOKEN_EXPIRE_MINUTES * 60


def set_password(conn, username: str, plain: str):
    """Store a new password for the account and sign out its existing sessions: every session token issued
    before this second is refused from now on. The caller commits."""
    conn.execute(
        "UPDATE users SET hashed_password = ?, password_changed_at = ?, must_change_password = 0 WHERE username = ?",
        (hash_password(plain), int(time.time()), username),
    )


def flag_weak_password(username: str):
    """The password just typed at sign-in no longer meets the policy: until it is changed, the account's sessions
    can only read who they are and change the password (see _session_user)."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET must_change_password = 1 WHERE username = ?", (username,))
        conn.commit()


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


_dummy_hash = None
_dummy_lock = threading.Lock()


def _unknown_user_hash():
    """A hash to check the password against when the account does not exist, so an unknown username costs the same
    bcrypt time as a wrong password: a fast answer told which usernames exist."""
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = hash_password(secrets.token_hex(16))
        return _dummy_hash


def authenticate_user(username: str, password: str):
    user = get_user(username)
    # Directory accounts, and names no account has yet, are checked by the LDAP directory when one is set up
    # (app/core/ldap_auth.py); a local or SSO account never is.
    if user is None or user.get("auth_source") == "ldap":
        from app.core import ldap_auth

        try:
            directory_user = ldap_auth.authenticate(username, password)
        except (ldap_auth.LdapError, ldap_auth.LocalAccountConflict) as e:
            logging.getLogger(__name__).warning("LDAP sign-in of %r failed: %s", username, e)
            directory_user = None
        if directory_user is not None:
            return directory_user
        if user is not None:
            return None
    if not user:
        verify_password(password, _unknown_user_hash())
        return None
    if not verify_password(password, user["hashed_password"]):
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


def _password_change_required():
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Password change required")


def _session_user(payload: dict, pending_ok: bool = False):
    """pending_ok: the endpoint stays open while the account must change its password (who am I, change it)."""
    username = payload.get("sub")
    # 2fa_pending: intermediate token (see create_preauth_token). It proves the
    # password but not the second factor, and must never be accepted as a normal
    # session token.
    if username is None or payload.get("2fa_pending"):
        raise _credentials_exception()
    if _session_revoked(payload.get("jti")):
        raise _credentials_exception()
    user = get_user(username)
    if user is None:
        raise _credentials_exception()
    # A password change or reset signs out every session opened before it (set_password). A token without
    # iat predates that mechanism and is refused as soon as the password has been changed once.
    changed = user.get("password_changed_at")
    if changed and int(payload.get("iat") or 0) < changed:
        raise _credentials_exception()
    if user.get("must_change_password") and not pending_ok:
        raise _password_change_required()
    return user


def _current_user(token: str, pending_ok: bool):
    payload = _decode_session(token)
    if payload is not None:
        return _session_user(payload, pending_ok)

    # Not a valid JWT: it may be an API token instead of a session token. Same
    # Authorization: Bearer header, different format ("hlt_" prefix), so no new
    # FastAPI dependency has to be wired everywhere, just a fallback here.
    from app.core.api_tokens import (
        verify_token,  # late import: avoids a cycle (api_tokens -> database, no way back to security)
    )

    user = verify_token(token)
    if user is None:
        raise _credentials_exception()
    # The same rule as a session: an account whose password must be changed can do nothing else, including
    # through a script's token.
    if user.get("must_change_password") and not pending_ok:
        raise _password_change_required()
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)):
    return _current_user(token, pending_ok=False)


async def get_current_user_pending_ok(token: str = Depends(oauth2_scheme)):
    """get_current_user that also lets through a session whose password must be changed: only for /auth/me."""
    return _current_user(token, pending_ok=True)


def _session_only(token: str, pending_ok: bool):
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
    return _session_user(payload, pending_ok)


async def get_session_user(token: str = Depends(oauth2_scheme)):
    return _session_only(token, pending_ok=False)


async def get_session_user_pending_ok(token: str = Depends(oauth2_scheme)):
    """get_session_user that also lets through a session whose password must be changed: only for the change."""
    return _session_only(token, pending_ok=True)


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


def optional_user(request):
    """The user behind the request's bearer token, or None for an anonymous or invalid
    one. For the few endpoints that answer everyone but tell a signed-in user more."""
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        return _current_user(token.strip(), pending_ok=True)
    except HTTPException:
        return None
