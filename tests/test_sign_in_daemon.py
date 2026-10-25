"""Signing in is hyperlite-daemon's from start to end (docs/design/privilege-separation.md, section 14.5): the API,
www-data from 4e, holds no proof before a session exists, so it may write nothing then. The daemon records the sign-in,
and for SSO opens the client secret, checks the identity provider's answer and makes or updates the account, as
pvedaemon makes Proxmox VE's ticket and writes user.cfg."""

from urllib.parse import parse_qs, urlparse

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged, sso, tickets
from app.daemon.registry import Context, DaemonError, dispatch


@pytest.fixture()
def from_www_data(monkeypatch):
    """The API as on a node once it runs as www-data: every sign-in and every write goes to the daemon's socket,
    stood in for here, and comes from uid 33 with the request's proof, when there is one."""
    asked = []

    def call(op, args=None, user=None, timeout=None):
        asked.append(op)
        proof = privileged.daemon_client._proof()
        answer = dispatch({"op": op, "args": args or {}}, Context(user, None, 1, 33, proof=proof))
        if not answer.get("ok"):
            raise DaemonError(answer["error"], answer.get("detail"))
        return answer["result"], []

    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.delenv(tickets.SIGNING_ENV)
    monkeypatch.setattr(privileged.daemon_client, "call_fds", call)
    return asked


def last_login(username):
    from app.repositories import registry

    return registry.accounts().sync.get(username)["last_login_at"]


def test_a_password_sign_in_is_recorded_by_the_daemon(client, make_user, from_www_data):
    make_user("ana", "viewer", "correct horse battery")
    assert last_login("ana") is None
    r = client.post("/auth/login", data={"username": "ana", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    assert last_login("ana") is not None
    assert from_www_data == ["auth.password"]  # nothing written by the API, which could not


@pytest.fixture()
def idp(database, monkeypatch):
    from app.core import secrets_crypto

    sso.set_config(
        enabled=True,
        issuer="https://idp.example/realms/x",
        client_id="hl",
        client_secret="s",
        redirect_uri="https://hv/auth/sso/callback",
    )
    monkeypatch.setattr(secrets_crypto, "decrypt", lambda value: "s")
    doc = {
        "issuer": "https://idp.example/realms/x",
        "authorization_endpoint": "https://idp.example/auth",
        "token_endpoint": "https://idp.example/token",
        "jwks_uri": "https://idp.example/jwks",
    }
    monkeypatch.setattr(sso, "discover", lambda issuer: doc)
    monkeypatch.setattr(sso, "exchange_code", lambda config, d, code: {"id_token": f"token-for-{code}"})
    claims = {"sub": "s-1", "preferred_username": "bea", "groups": []}
    monkeypatch.setattr(sso, "validate_id_token", lambda config, d, token, nonce: claims)
    return claims


def test_an_sso_sign_in_is_the_daemons_from_the_redirect_to_the_session(root_client, client, idp, from_www_data):
    r = root_client.get("/auth/sso/login", follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = root_client.get("/auth/sso/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "/?sso=1", r.headers["location"]
    handoff = r.cookies.get("hl_sso_handoff") or root_client.cookies.get("hl_sso_handoff")
    client.cookies.set("hl_sso_handoff", handoff)
    r = client.post("/auth/sso/exchange")
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "bea" and last_login("bea") is not None
    # The account made, its sign-in recorded: each by the daemon's own sign-in operations, none by an access.* write.
    assert from_www_data == ["auth.sso_start", "auth.sso_callback", "auth.sso"]


def test_a_callback_from_another_browser_is_refused_and_said(root_client, idp, from_www_data):
    r = root_client.get("/auth/sso/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    root_client.cookies.clear()  # the callback link opened elsewhere: no binding cookie
    r = root_client.get("/auth/sso/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "sso_error" in r.headers["location"]
    from app.repositories import registry

    assert registry.accounts().sync.get("bea") is None


def test_without_the_daemon_nobody_signs_in_through_sso(root_client, idp, monkeypatch, tmp_path):
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.delenv(tickets.SIGNING_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    assert root_client.get("/auth/sso/login", follow_redirects=False).status_code == 503
