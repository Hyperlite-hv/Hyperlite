"""root on a node, through hyperlite-admin (docs/design/admin-cli.md, 4.3): hyperlite-daemon signs a short session for
root@<node> to a process the kernel says runs as root, the API takes it as an administrator with no row in `users`, and
the other members accept it only when that node signed it."""

import time

import jwt
import pytest
from test_peer_auth import ME, Member, cfs  # noqa: F401  (cfs: the fixture of the members' published keys)

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import peer_auth, security, sign_in, tickets
from app.core.api_tokens import create_token
from app.daemon.registry import Context, dispatch

ROOT = f"root@{ME}"


def _ask(uid):
    return dispatch(
        {"op": "auth.local", "args": {}}, Context(user="root", request_id=None, peer_pid=4242, peer_uid=uid)
    )


def _root_ticket():
    answer = _ask(0)
    assert answer["ok"], answer
    return answer["result"]["token"]


def _me(client, token):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_the_daemon_signs_roots_session_for_root_only(database):
    answer = _ask(0)
    assert answer["ok"] and answer["result"]["expires_in_s"] == sign_in.LOCAL_ROOT_LIFETIME_S
    claims = jwt.decode(answer["result"]["token"], options={"verify_signature": False})
    assert claims["sub"] == ROOT and claims["exp"] - claims["iat"] == sign_in.LOCAL_ROOT_LIFETIME_S
    # The API's own user, once it runs without root, gets nothing whatever it asks.
    assert _ask(998) == {"ok": False, "error": "forbidden", "detail": "Only root on this node gets its session"}


def test_roots_session_is_an_administrator_named_after_its_node(client, database):
    r = _me(client, _root_ticket())
    assert r.status_code == 200, r.text
    assert (r.json()["username"], r.json()["role"]) == (ROOT, "admin")


def test_what_root_does_is_audited_under_its_name(client, database, monkeypatch):
    seen = []
    monkeypatch.setattr("app.routers.pools.log_action", lambda user, action, *a, **k: seen.append((user, action)))
    r = client.post("/pools", json={"name": "lab"}, headers={"Authorization": f"Bearer {_root_ticket()}"})
    assert r.status_code == 201, r.text
    assert seen == [(ROOT, "create_pool")]


def test_root_has_no_account_to_manage(client, database):
    """No API token, password or second factor for root of a node: what a person does for their own account."""
    headers = {"Authorization": f"Bearer {_root_ticket()}"}
    r = client.post("/auth/tokens", json={"name": "cron"}, headers=headers)
    assert r.status_code == 403 and "root of a node" in r.json()["detail"]


def _signed_here(claims, life=300):
    now = int(time.time())
    return tickets.sign_local({"iat": now, "exp": now + life, **claims})


def test_a_root_ticket_names_only_the_node_that_signed_it(client, database):
    forged = _signed_here({"sub": "root@pve-a", "role": "admin", security.LOCAL_ROOT_CLAIM: True})
    assert _me(client, forged).status_code == 401


def test_the_name_of_root_is_reserved(client, database, make_user):
    """An account of a directory or an identity provider called root@<node> never reads as root in the audit: not
    through a session, nor through an API token."""
    make_user(ROOT, "admin")
    assert _me(client, _signed_here({"sub": ROOT, "role": "admin"})).status_code == 401
    _, token = create_token(ROOT, "script")
    assert _me(client, token).status_code == 401
    assert _me(client, _signed_here({"sub": "Root@pve-a", "role": "admin"})).status_code == 401


def test_root_acts_on_another_member_as_root_of_its_own_node(database, cfs):  # noqa: F811
    ticket = _root_ticket()
    out = peer_auth.peer(ticket, ["pve-a"])
    claims = jwt.decode(out["pve-a"], options={"verify_signature": False})
    assert (claims["iss"], claims["sub"], claims[security.LOCAL_ROOT_CLAIM]) == (ME, ROOT, True)
    task = peer_auth.delegate(ticket)
    assert peer_auth._account(task)["username"] == ROOT  # a rolling update root starts runs as root


@pytest.mark.parametrize(
    "sub, claim, status",
    [("root@pve-a", True, 200), ("root@pve-b", True, 401), ("root@pve-a", False, 401), (ROOT, True, 401)],
)
def test_a_member_is_accepted_as_root_of_the_member_that_signed_only(client, database, cfs, sub, claim, status):  # noqa: F811
    a = Member(cfs, "pve-a")
    extra = {security.LOCAL_ROOT_CLAIM: True} if claim else {}
    r = _me(client, a.ticket(sub, **extra))
    assert r.status_code == status, r.text
    if status == 200:
        assert r.json()["username"] == "root@pve-a"
