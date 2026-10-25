"""The caller's proof with every call (docs/design/privilege-separation.md, section 14, PR 4a): the API passes the
credential of the request it serves, and hyperlite-daemon checks it itself. In this first step it is logged, not
required: the next ones refuse a call without one."""

import logging

import pytest

from app.core import peer_auth, privileged
from app.daemon.registry import proven


def _token(headers):
    return headers["Authorization"].split(" ", 1)[1]


def test_a_session_proves_its_own_account_and_nothing_else(auth_headers):
    alice = _token(auth_headers("alice"))
    assert proven(alice) == "alice"
    assert proven(alice[:-4] + "AAAA") is None, "a forged signature proves nothing"
    assert proven("not-a-token") is None and proven(None) is None


def test_an_api_token_proves_its_account(client, auth_headers):
    created = client.post("/auth/tokens", json={"name": "ci", "expires_days": 1}, headers=auth_headers("alice"))
    assert created.status_code in (200, 201), created.text
    assert proven(created.json()["token"]) == "alice"


@pytest.fixture()
def logged(caplog):
    caplog.set_level(logging.INFO, logger="app.daemon.registry")
    return caplog


def _ping_line(caplog):
    return next(r.getMessage() for r in reversed(caplog.records) if r.getMessage().startswith("system.ping by"))


def test_the_daemon_logs_whether_the_api_proved_who_asks(auth_headers, logged):
    alice = _token(auth_headers("alice"))
    bob = _token(auth_headers("bob"))
    peer_auth.remember(alice)
    privileged.run("system.ping", user="alice")
    assert "[" not in _ping_line(logged), "the named user, proven: nothing to say"
    peer_auth.remember(bob)
    privileged.run("system.ping", user="alice")
    assert _ping_line(logged).endswith("[proof of bob]")
    peer_auth.remember(None)
    privileged.run("system.ping", user="alice")
    assert _ping_line(logged).endswith("[no proof]")


def test_the_proof_crosses_the_socket(auth_headers, tmp_path, monkeypatch, logged):
    import os
    import threading

    from app.core import daemon_client
    from app.daemon import SOCKET_ENV, server

    path = tmp_path / "daemon.sock"
    monkeypatch.setenv(SOCKET_ENV, str(path))
    srv = server.Server(str(path), server.Handler)
    srv.allowed_uids = lambda: {0, os.getuid()}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        peer_auth.remember(_token(auth_headers("alice")))
        daemon_client.call("system.ping", user="alice")
        assert "[" not in _ping_line(logged)
    finally:
        srv.shutdown()
        srv.server_close()
        peer_auth.remember(None)
