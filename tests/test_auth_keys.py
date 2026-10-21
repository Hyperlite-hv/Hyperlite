"""Sessions signed with key pairs held by hyperlite-daemon, verified by the API with the public keys only
(app/core/auth_keys.py, docs/design/privilege-separation.md, section 12)."""

import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import auth_keys, paths, security
from app.daemon.registry import Context, dispatch


@pytest.fixture()
def keys(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ETC", tmp_path / "etc")
    monkeypatch.setattr(paths, "PRIV", tmp_path / "etc" / "priv")
    auth_keys._cache.clear()
    return tmp_path / "etc"


def _kid(token):
    return jwt.get_unverified_header(token)["kid"]


def _claims(**extra):
    return {"sub": "alice", "exp": int(time.time()) + 3600, **extra}


def test_a_token_names_its_key_and_is_verified_with_the_public_key_only(keys):
    token = auth_keys.sign_local(_claims())
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    os.unlink(auth_keys.private_path())  # what the API will have from step 4: the public keys, nothing else
    assert auth_keys.decode(token)["sub"] == "alice"


def test_the_private_key_is_root_only_the_public_keys_readable(keys):
    auth_keys.ensure_keys()
    assert auth_keys.private_path().stat().st_mode & 0o777 == 0o600
    assert paths.PRIV.stat().st_mode & 0o777 == 0o700
    assert auth_keys.public_path().stat().st_mode & 0o777 == 0o644


def test_a_new_pair_every_day_and_a_session_of_a_week_outlives_the_rotations(keys, monkeypatch):
    start = time.time()
    monkeypatch.setattr(auth_keys.time, "time", lambda: start)
    old = auth_keys.sign_local(_claims(exp=int(start) + 7 * 24 * 3600))
    for day in range(1, 8):
        monkeypatch.setattr(auth_keys.time, "time", lambda d=day: start + d * 24 * 3600 + 1)
        assert _kid(auth_keys.sign_local(_claims(exp=int(start) + 8 * 24 * 3600))) != _kid(old)
    assert len({k["kid"] for k in auth_keys._public_keys()}) == 8
    assert auth_keys._verification_key(_kid(old)) is not None  # still verifiable 7 days later


def test_a_key_retired_long_ago_is_dropped(keys):
    now = int(time.time())
    auth_keys.ensure_keys(now - 20 * 24 * 3600)
    first = auth_keys._public_keys()[0]["kid"]
    auth_keys.ensure_keys(now - 10 * 24 * 3600)  # its successor appeared 10 days ago: past KEEP_S
    auth_keys.ensure_keys(now)
    assert first not in {k["kid"] for k in auth_keys._public_keys()}


def test_a_token_signed_with_another_key_is_refused(keys):
    auth_keys.ensure_keys()
    kid = auth_keys._public_keys()[0]["kid"]
    forged = jwt.encode(_claims(), Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": kid})
    assert auth_keys.decode(forged) is None
    unknown = jwt.encode(_claims(), Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "0" * 16})
    assert auth_keys.decode(unknown) is None
    assert auth_keys.decode("not a token") is None


def test_a_call_between_members_still_carries_the_shared_key(database):
    """Until each member verifies the others' own keys (section 12, PR C)."""
    token = security.create_peer_token({"sub": "admin", "role": "admin"})
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert security.decode_token(token)["sub"] == "admin"


def test_no_session_without_the_daemon(monkeypatch, tmp_path):
    monkeypatch.delenv(auth_keys.SIGNING_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    with pytest.raises(HTTPException) as exc:
        security.create_access_token({"sub": "alice"})
    assert exc.value.status_code == 503 and "hyperlite-daemon" in exc.value.detail


def _sign_op(claims):
    return dispatch({"op": "auth.sign", "args": {"claims": claims}}, Context("alice", None, 1, 0))


def test_the_daemon_signs_a_session_and_refuses_one_without_account_or_too_long(keys):
    answer = _sign_op(_claims())
    assert answer["ok"] and auth_keys.decode(answer["result"])["sub"] == "alice"
    assert _sign_op({"exp": int(time.time()) + 60})["error"] == "invalid_arguments"
    assert _sign_op(_claims(exp=int(time.time()) + 30 * 24 * 3600))["error"] == "invalid_arguments"
    assert _sign_op(_claims(exp=int(time.time()) - 60))["error"] == "invalid_arguments"
