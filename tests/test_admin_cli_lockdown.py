"""hyperlite-admin's lockdown (app/core/admin_cli.py, docs/design/admin-cli.md 4.3), as ESXi's lockdown mode: turned
on by an administrator, root of a node gets no session and loses the one it holds, on every member; a named account's
API token still works."""

from test_peer_auth import Member, cfs  # noqa: F401  (cfs: the fixture of the members' published keys)

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import admin_cli
from app.core.api_tokens import create_token
from app.daemon.registry import Context, dispatch


def _ask():
    return dispatch({"op": "auth.local", "args": {}}, Context(user="root", request_id=None, peer_pid=4242, peer_uid=0))


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_it_is_off_until_an_administrator_turns_it_on(client, auth_headers, monkeypatch):
    admin = auth_headers("ana", "admin")
    assert client.get("/admin-cli", headers=admin).json() == {"lockdown": False}
    assert _ask()["ok"]
    seen = []
    monkeypatch.setattr("app.routers.admin_cli.log_action", lambda *a, **k: seen.append(a))
    r = client.put("/admin-cli", json={"lockdown": True}, headers=admin)
    assert r.status_code == 200 and r.json() == {"lockdown": True}
    assert client.get("/admin-cli", headers=admin).json() == {"lockdown": True}
    assert seen == [("ana", "admin_cli_lockdown", "api", "success", "on")]


def test_only_an_administrator_reads_or_sets_it(client, auth_headers):
    viewer = auth_headers("vic", "viewer")
    assert client.get("/admin-cli", headers=viewer).status_code == 403
    assert client.put("/admin-cli", json={"lockdown": True}, headers=viewer).status_code == 403
    assert admin_cli.locked() is False


def test_locked_root_gets_no_session_and_loses_the_one_it_holds(client, database):
    held = _ask()["result"]["token"]
    assert client.get("/auth/me", headers=_bearer(held)).status_code == 200
    admin_cli.set_locked(True)
    answer = _ask()
    assert answer["ok"] is False and answer["error"] == "forbidden" and "--token-file" in answer["detail"]
    r = client.get("/auth/me", headers=_bearer(held))
    assert r.status_code == 403 and "Lockdown is on" in r.json()["detail"]
    admin_cli.set_locked(False)
    assert client.get("/auth/me", headers=_bearer(held)).status_code == 200


def test_a_named_accounts_token_still_works(client, make_user, database):
    make_user("ops", "admin")
    _, token = create_token("ops", "cli")
    admin_cli.set_locked(True)
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 200 and r.json()["username"] == "ops"


def test_another_members_root_is_refused_too(client, database, cfs):  # noqa: F811
    from app.core import security

    a = Member(cfs, "pve-a")
    ticket = a.ticket("root@pve-a", **{security.LOCAL_ROOT_CLAIM: True})
    assert client.get("/auth/me", headers=_bearer(ticket)).status_code == 200
    admin_cli.set_locked(True)  # replicated: what every member reads
    assert client.get("/auth/me", headers=_bearer(ticket)).status_code == 403
