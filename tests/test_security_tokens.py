"""Session and pre-authentication JWT handling."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException

from app.core import security


def _authenticate(token):
    return asyncio.run(security.get_current_user(token))


@pytest.fixture()
def admin_user(database):
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
            ("alice", security.hash_password("correct horse"), "admin"),
        )
        conn.commit()
    return "alice"


def test_valid_session_token_authenticates_the_user(admin_user):
    token = security.create_access_token({"sub": admin_user})
    user = _authenticate(token)
    assert user["username"] == "alice"
    assert user["role"] == "admin"


def test_session_token_carries_an_expiry():
    token = security.create_access_token({"sub": "alice"})
    claims = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
    assert claims["exp"] > datetime.now(UTC).timestamp()


def test_expired_token_is_rejected(admin_user):
    expired = jwt.encode(
        {"sub": admin_user, "exp": datetime.now(UTC) - timedelta(minutes=1)},
        security.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        _authenticate(expired)
    assert exc.value.status_code == 401


def test_token_signed_with_another_secret_is_rejected(admin_user):
    forged = jwt.encode(
        {"sub": admin_user, "exp": datetime.now(UTC) + timedelta(hours=1)},
        "another-secret-key-0123456789abcdef0123456789",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        _authenticate(forged)
    assert exc.value.status_code == 401


def test_unsigned_token_with_alg_none_is_rejected(admin_user):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': admin_user})}."
    with pytest.raises(HTTPException) as exc:
        _authenticate(unsigned)
    assert exc.value.status_code == 401


def test_token_for_an_unknown_user_is_rejected(database):
    token = security.create_access_token({"sub": "ghost"})
    with pytest.raises(HTTPException) as exc:
        _authenticate(token)
    assert exc.value.status_code == 401


def test_pre_auth_token_is_never_accepted_as_a_session_token(admin_user):
    pre_auth = security.create_preauth_token(admin_user)
    with pytest.raises(HTTPException) as exc:
        _authenticate(pre_auth)
    assert exc.value.status_code == 401


def test_pre_auth_token_is_short_lived():
    token = security.create_preauth_token("alice")
    claims = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
    remaining = claims["exp"] - datetime.now(UTC).timestamp()
    assert claims["2fa_pending"] is True
    assert 0 < remaining <= 5 * 60


def test_password_hashing_round_trip():
    hashed = security.hash_password("s3cret-passphrase")
    assert hashed != "s3cret-passphrase"
    assert security.verify_password("s3cret-passphrase", hashed)
    assert not security.verify_password("wrong", hashed)
