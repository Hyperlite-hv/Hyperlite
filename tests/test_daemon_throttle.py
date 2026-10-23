"""hyperlite-daemon counts wrong proofs itself (GHSA-83f3-qj22-m659): the API's locks are a first filter only, and a
process that took over the API could otherwise guess at socket speed."""

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import paths, security, tickets, twofa
from app.daemon.ops import auth
from app.daemon.registry import Context, dispatch


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ETC", tmp_path / "etc")
    monkeypatch.setattr(paths, "PRIV", tmp_path / "etc" / "priv")
    tickets._cache.clear()
    monkeypatch.setattr(auth, "_failures", auth._Failures())  # never left to another test


def _op(op, **args):
    return dispatch({"op": op, "args": args}, Context(None, None, 1, 0))["result"]


def test_too_many_wrong_passwords_lock_the_account_whatever_its_case(make_user, monkeypatch):
    make_user("alice", "admin", "correct horse battery")
    monkeypatch.setattr(auth, "FAILURES_MAX", 3)
    for name in ("alice", "Alice", "ALICE"):
        assert _op("auth.password", username=name, password="wrong")["result"] == "invalid"
    assert _op("auth.password", username="alice", password="correct horse battery")["result"] == "locked"
    assert _op("auth.password", username="bob", password="wrong")["result"] == "invalid"  # another account


def test_a_pre_authentication_token_gets_a_few_codes_only(make_user, monkeypatch):
    make_user("alice", "admin", "correct horse battery")
    monkeypatch.setattr(security, "get_user", lambda name: {**security._accounts().get(name), "totp_enabled": 1})
    monkeypatch.setattr(twofa, "verify_code", lambda user, secret, code: code == "123456")
    preauth = _op("auth.password", username="alice", password="correct horse battery")["preauth_token"]
    for _ in range(auth.FAILURES_PER_TOKEN):
        assert _op("auth.totp", preauth_token=preauth, code="000000")["result"] == "invalid"
    assert _op("auth.totp", preauth_token=preauth, code="123456")["result"] == "locked"


def test_old_failures_are_forgotten(make_user, monkeypatch):
    make_user("alice", "admin", "correct horse battery")
    monkeypatch.setattr(auth, "FAILURES_MAX", 1)
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    _op("auth.password", username="alice", password="wrong")
    assert _op("auth.password", username="alice", password="correct horse battery")["result"] == "locked"
    clock[0] += auth.FAILURES_WINDOW_S + 1
    assert _op("auth.password", username="alice", password="correct horse battery")["result"] == "session"
