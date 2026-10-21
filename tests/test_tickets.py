"""Sessions signed with key pairs held by hyperlite-daemon, verified by the API with the public keys only
(app/core/tickets.py, docs/design/privilege-separation.md, section 12)."""

import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import paths, security, tickets
from app.daemon.registry import Context, dispatch


@pytest.fixture()
def keys(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ETC", tmp_path / "etc")
    monkeypatch.setattr(paths, "PRIV", tmp_path / "etc" / "priv")
    tickets._cache.clear()
    return tmp_path / "etc"


def _kid(token):
    return jwt.get_unverified_header(token)["kid"]


def _claims(**extra):
    return {"sub": "alice", "exp": int(time.time()) + 3600, **extra}


def test_a_token_names_its_key_and_is_verified_with_the_public_key_only(keys):
    token = tickets.sign_local(_claims())
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    os.unlink(tickets.private_path())  # what the API will have from step 4: the public keys, nothing else
    assert tickets.decode(token)["sub"] == "alice"


def test_the_private_key_is_root_only_the_public_keys_readable(keys):
    tickets.ensure_keys()
    assert tickets.private_path().stat().st_mode & 0o777 == 0o600
    assert paths.PRIV.stat().st_mode & 0o777 == 0o700
    assert tickets.public_path().stat().st_mode & 0o777 == 0o644


def test_a_new_pair_every_day_and_a_session_of_a_week_outlives_the_rotations(keys, monkeypatch):
    start = time.time()
    monkeypatch.setattr(tickets.time, "time", lambda: start)
    old = tickets.sign_local(_claims(exp=int(start) + 7 * 24 * 3600))
    for day in range(1, 8):
        monkeypatch.setattr(tickets.time, "time", lambda d=day: start + d * 24 * 3600 + 1)
        assert _kid(tickets.sign_local(_claims(exp=int(start) + 8 * 24 * 3600))) != _kid(old)
    assert len({k["kid"] for k in tickets._public_keys()}) == 8
    assert tickets._verification_key(_kid(old)) is not None  # still verifiable 7 days later


def test_a_key_retired_long_ago_is_dropped(keys):
    now = int(time.time())
    tickets.ensure_keys(now - 20 * 24 * 3600)
    first = tickets._public_keys()[0]["kid"]
    tickets.ensure_keys(now - 10 * 24 * 3600)  # its successor appeared 10 days ago: past KEEP_S
    tickets.ensure_keys(now)
    assert first not in {k["kid"] for k in tickets._public_keys()}


def test_a_token_signed_with_another_key_is_refused(keys):
    tickets.ensure_keys()
    kid = tickets._public_keys()[0]["kid"]
    forged = jwt.encode(_claims(), Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": kid})
    assert tickets.decode(forged) is None
    unknown = jwt.encode(_claims(), Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "0" * 16})
    assert tickets.decode(unknown) is None
    assert tickets.decode("not a token") is None


def test_a_call_between_members_still_carries_the_shared_key(database):
    """Until each member verifies the others' own keys (section 12, PR C)."""
    token = security.create_peer_token({"sub": "admin", "role": "admin"})
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert security.decode_token(token)["sub"] == "admin"


def test_no_session_without_the_daemon(client, make_user, monkeypatch, tmp_path):
    make_user("alice", "admin", "correct horse battery")
    monkeypatch.delenv(tickets.SIGNING_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    r = client.post("/auth/login", data={"username": "alice", "password": "correct horse battery"})
    assert r.status_code == 503 and "hyperlite-daemon" in r.json()["detail"]


def test_outside_the_tests_nothing_signs_claims_it_is_handed(monkeypatch):
    """A session is opened by the daemon once it checked a proof (app/core/sign_in.py), never for given claims."""
    monkeypatch.delenv(tickets.SIGNING_ENV)
    with pytest.raises(RuntimeError):
        tickets.sign(_claims())


def _op(op, **args):
    return dispatch({"op": op, "args": args}, Context(None, None, 1, 0))


def test_the_daemon_signs_only_after_checking_the_password(make_user, keys):
    make_user("alice", "admin", "correct horse battery")
    assert _op("auth.sign", claims=_claims())["error"] == "unknown_operation"  # PR A's operation is gone
    assert _op("auth.password", username="alice", password="wrong")["result"] == {"result": "invalid"}
    answer = _op("auth.password", username="alice", password="correct horse battery")["result"]
    assert answer["result"] == "session" and answer["user"] == {
        "username": "alice",
        "role": "admin",
        "password_change_required": False,
    }
    assert tickets.decode(answer["token"])["sub"] == "alice"
    assert _op("auth.password", username="alice", password="x" * 5000)["error"] == "invalid_arguments"


def test_a_pre_authentication_token_is_no_session_and_the_code_is_checked_by_the_daemon(make_user, keys, monkeypatch):
    make_user("alice", "admin", "correct horse battery")
    from app.core import twofa

    monkeypatch.setattr(security, "get_user", lambda name: {**security._accounts().get(name), "totp_enabled": 1})
    answer = _op("auth.password", username="alice", password="correct horse battery")["result"]
    assert answer["result"] == "second_factor" and answer["methods"] == ["totp"]
    assert tickets.decode(answer["preauth_token"])["2fa_pending"] is True
    monkeypatch.setattr(twofa, "verify_code", lambda user, secret, code: code == "123456")
    assert _op("auth.totp", preauth_token=answer["preauth_token"], code="000000")["result"]["result"] == "invalid"
    session = _op("auth.totp", preauth_token=answer["preauth_token"], code="123456")["result"]
    assert session["result"] == "session" and "2fa_pending" not in tickets.decode(session["token"])
    assert _op("auth.totp", preauth_token=session["token"], code="123456")["result"]["result"] == "expired"
