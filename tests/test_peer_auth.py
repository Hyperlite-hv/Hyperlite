"""Calls between members signed with the calling member's own keys (app/core/peer_auth.py,
docs/design/privilege-separation.md, section 12.1): each member publishes its public keys, a member accepts a call only
with the keys of the member that signed it and only for itself, and a daemon signs only for a credential it checked.
HS256 with the shared key only during a rolling update from an older version, and for the sessions signed before."""

import base64
import json
import threading
import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import cluster_setup, paths, peer_auth, security, tickets
from app.core.cfs_client import NotFound
from app.daemon.registry import Context, dispatch

ME = "hv-test"  # this node in the tests (tests/conftest.py)


class FakeCfs:
    def __init__(self):
        self.entries = {}

    def get(self, path):
        if path not in self.entries:
            raise NotFound(2, path)
        return types.SimpleNamespace(data=self.entries[path])

    def put(self, path, data, expected=None):
        self.entries[path] = data

    def delete(self, path, expected=None):
        if self.entries.pop(path, None) is None:
            raise NotFound(2, path)


class Member:
    """Another member: its own key pair, published as its daemon would."""

    def __init__(self, cfs, name):
        self.name = name
        self.private = Ed25519PrivateKey.generate()
        raw = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.kid = tickets._kid(raw)
        self.entry = {"kid": self.kid, "public": base64.b64encode(raw).decode(), "created": int(time.time())}
        cfs.entries[f"{peer_auth.STATE_DIR}/{name}"] = json.dumps({"keys": [self.entry]}).encode()

    def ticket(self, sub, aud=ME, life=peer_auth.PEER_LIFETIME_S, kid=None, private=None, **extra):
        now = int(time.time())
        claims = {"typ": "peer", "iss": self.name, "aud": aud, "sub": sub, "iat": now, "exp": now + life, **extra}
        return jwt.encode(claims, private or self.private, algorithm="EdDSA", headers={"kid": kid or self.kid})


@pytest.fixture()
def cfs(monkeypatch, tmp_path):
    fake = FakeCfs()
    monkeypatch.setattr(peer_auth, "_client", lambda: fake)
    monkeypatch.setattr(peer_auth, "_mode", lambda: "cluster")
    members = [{"name": n, "nodeid": i} for i, n in enumerate((ME, "pve-a", "pve-b"), 1)]
    monkeypatch.setattr(cluster_setup, "shared_conf", lambda: {"cluster": "lab", "version": 3, "members": members})
    monkeypatch.setattr(paths, "VAR", tmp_path / "var")
    fake.members = members
    return fake


def _me(client, token):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def _hs256(sub, iat=None):
    now = int(time.time())
    iat = now if iat is None else iat
    return jwt.encode({"sub": sub, "iat": iat, "exp": now + 3600}, security.SECRET_KEY, algorithm="HS256")


def _publish_all(cfs):
    """Every other member runs this version: it published its keys."""
    return {n: Member(cfs, n) for n in ("pve-a", "pve-b")}


# ---- Publishing ----


def test_this_node_publishes_its_public_keys_once(cfs):
    tickets.ensure_keys()
    peer_auth.publish()
    entry = json.loads(cfs.entries[f"{peer_auth.STATE_DIR}/{ME}"])
    assert [k["kid"] for k in entry["keys"]] == [k["kid"] for k in tickets._public_keys()]
    assert "BEGIN" not in json.dumps(entry)  # public keys only
    cfs.put = lambda *a, **k: pytest.fail("published again unchanged")
    peer_auth.publish()


def test_a_member_removed_takes_its_keys_with_it(cfs):
    Member(cfs, "pve-a")
    peer_auth.forget("pve-a")
    assert peer_auth.published("pve-a") is None
    peer_auth.forget("pve-a")  # already gone: nothing to say


# ---- The member called ----


def test_a_call_signed_by_a_member_for_this_node_is_a_session_of_its_user(client, make_user, cfs):
    make_user("ana", "viewer")
    a = Member(cfs, "pve-a")
    r = _me(client, a.ticket("ana"))
    assert r.status_code == 200 and r.json()["username"] == "ana"


@pytest.mark.parametrize(
    "case",
    ["other_audience", "not_a_member", "key_of_another_member", "unknown_key", "too_long", "expired", "no_type"],
)
def test_a_call_is_refused_unless_its_member_signed_it_for_this_node(client, make_user, cfs, case):
    make_user("ana", "admin")
    a, b = Member(cfs, "pve-a"), Member(cfs, "pve-b")
    token = {
        "other_audience": lambda: a.ticket("ana", aud="pve-b"),
        "not_a_member": lambda: Member(cfs, "pve-old").ticket("ana"),
        "key_of_another_member": lambda: a.ticket("ana", kid=b.kid, private=b.private),
        "unknown_key": lambda: a.ticket("ana", kid="0" * 16, private=Ed25519PrivateKey.generate()),
        "too_long": lambda: a.ticket("ana", life=3600),
        "expired": lambda: a.ticket("ana", life=-3600 + peer_auth.PEER_LIFETIME_S, iat=int(time.time()) - 3600),
        "no_type": lambda: a.ticket("ana", typ=None),
    }[case]()
    assert _me(client, token).status_code == 401


def test_tickets_this_node_signs_for_others_are_no_session_here(client, make_user, cfs, monkeypatch):
    make_user("ana", "admin")
    monkeypatch.setattr(peer_auth, "_members", lambda: ["pve-a"])
    session = security.create_access_token({"sub": "ana"})
    peer_auth.remember(session)
    tickets_for = peer_auth.peer(session, ["pve-a"])
    assert _me(client, tickets_for["pve-a"]).status_code == 401  # signed here for pve-a
    assert _me(client, peer_auth.delegate(session)).status_code == 401  # a task ticket proves an account to the daemon


# ---- The daemon signs only for a credential it checked ----


def test_the_daemon_signs_one_ticket_per_member_for_the_account_the_credential_proves(make_user, cfs):
    make_user("ana", "viewer")
    session = security.create_access_token({"sub": "ana"})
    out = peer_auth.peer(session, ["pve-a", "pve-b"])
    assert set(out) == {"pve-a", "pve-b"}
    own = {k["kid"]: k for k in tickets._public_keys()}
    for target, token in out.items():
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert header["kid"] in own
        assert (claims["typ"], claims["iss"], claims["aud"], claims["sub"]) == ("peer", ME, target, "ana")
        assert claims["exp"] - claims["iat"] == peer_auth.PEER_LIFETIME_S and claims["uid"]
    assert peer_auth.published(ME)  # published before the members see a ticket signed with it


@pytest.mark.parametrize("credential", ["garbage", "peer_ticket", "pending_password", "revoked"])
def test_the_daemon_refuses_what_proves_no_account(client, make_user, cfs, database, credential):
    make_user("ana", "admin")
    session = security.create_access_token({"sub": "ana"})
    if credential == "peer_ticket":
        session = Member(cfs, "pve-a").ticket("ana")
    elif credential == "pending_password":
        with database.get_conn() as conn:
            conn.execute("UPDATE users SET must_change_password = 1 WHERE username = 'ana'")
            conn.commit()
    elif credential == "revoked":
        security.revoke_session(session)
    elif credential == "garbage":
        session = "not a token"
    answer = dispatch(
        {"op": "auth.peer", "args": {"credential": session, "targets": ["pve-a"]}}, Context(None, None, 1, 0)
    )
    assert answer["ok"] is False and answer["error"] == "forbidden", answer


def test_the_daemon_signs_only_for_members(make_user, cfs):
    make_user("ana", "admin")
    session = security.create_access_token({"sub": "ana"})
    with pytest.raises(PermissionError, match="pve-old"):
        peer_auth.peer(session, ["pve-a", "pve-old"])


def test_a_task_ticket_is_an_administrators_and_lets_a_run_call_the_members(make_user, cfs):
    make_user("ana", "admin")
    make_user("vic", "viewer")
    task = peer_auth.delegate(security.create_access_token({"sub": "ana"}))
    claims = jwt.decode(task, options={"verify_signature": False})
    assert claims["typ"] == "task" and claims["exp"] - claims["iat"] == peer_auth.TASK_LIFETIME_S
    assert jwt.decode(peer_auth.peer(task, ["pve-a"])["pve-a"], options={"verify_signature": False})["sub"] == "ana"
    with pytest.raises(PermissionError):
        peer_auth.delegate(task)  # not from a task ticket
    with pytest.raises(PermissionError):
        peer_auth.delegate(security.create_access_token({"sub": "vic"}))


def test_a_task_ticket_ends_with_a_password_change(make_user, cfs):
    make_user("ana", "admin")
    task = peer_auth.delegate(security.create_access_token({"sub": "ana"}))
    time.sleep(1.1)  # whole seconds: the change comes after the ticket
    security.set_password("ana", "another horse battery")
    with pytest.raises(PermissionError):
        peer_auth.peer(task, ["pve-a"])


# ---- The calling member ----


def test_a_member_that_published_keys_gets_a_peer_ticket_an_older_one_the_shared_key(make_user, cfs):
    make_user("ana", "admin")
    Member(cfs, "pve-a")
    peer_auth.remember(security.create_access_token({"sub": "ana"}))
    new = peer_auth.ticket_for({"name": "pve-a"}, "ana")
    old = peer_auth.ticket_for({"name": "pve-b"}, "ana")
    assert jwt.get_unverified_header(new)["alg"] == "EdDSA"
    assert jwt.decode(new, options={"verify_signature": False})["aud"] == "pve-a"
    assert jwt.get_unverified_header(old)["alg"] == "HS256"


def test_no_ticket_without_a_credential(cfs):
    Member(cfs, "pve-a")
    peer_auth.CREDENTIAL.set(None)
    with pytest.raises(peer_auth.NoTicket):
        peer_auth.ticket_for({"name": "pve-a"}, "ana")


def test_the_credential_follows_the_threads_that_call_the_members():
    peer_auth.remember("the request's")
    seen = {}
    t = threading.Thread(target=peer_auth.carry(lambda: seen.update(one=peer_auth.CREDENTIAL.get())))
    t.start()
    t.join()
    t = threading.Thread(target=peer_auth.carry(lambda: seen.update(two=peer_auth.CREDENTIAL.get()), "a task's"))
    t.start()
    t.join()
    assert seen == {"one": "the request's", "two": "a task's"}


def test_a_cluster_view_calls_each_member_with_a_ticket_for_it(client, auth_headers, cfs, monkeypatch):
    from app.core import cluster, cluster_view, peer_https

    monkeypatch.setattr(cluster_view, "_mode", lambda: "cluster")
    headers = auth_headers("ana", role="viewer")
    _publish_all(cfs)
    nodes = [{"name": n, "hostname": f"{n}.lan", "ssh_user": "root", "ssh_port": 22, "status": "online"}
             for n in ("pve-a", "pve-b")]  # fmt: skip
    monkeypatch.setattr(cluster, "list_nodes", lambda: nodes)
    seen = {}

    def call(node, token, method, path, body=None, timeout=60, connect_timeout=5):
        seen[node["name"]] = jwt.decode(token, options={"verify_signature": False})
        return 200, {"items": [], "total": 0}

    monkeypatch.setattr(peer_https, "call", call)
    r = client.get("/tasks?cluster=true", headers=headers)
    assert r.status_code == 200 and r.json()["unreachable"] == []
    assert {n: (c["typ"], c["aud"], c["sub"]) for n, c in seen.items()} == {
        "pve-a": ("peer", "pve-a", "ana"), "pve-b": ("peer", "pve-b", "ana")
    }  # fmt: skip


# ---- HS256, for a transition only ----


def test_the_shared_key_is_accepted_while_a_member_has_not_published_keys(client, make_user, cfs):
    make_user("ana", "admin")
    Member(cfs, "pve-a")  # pve-b runs an older version
    assert _me(client, _hs256("ana")).status_code == 200
    Member(cfs, "pve-b")  # now every member runs this one
    assert _me(client, _hs256("ana")).status_code == 401


def test_an_hs256_session_is_never_accepted_on_its_own_word(client, make_user, cfs):
    # Its signing time is whatever the token says: any holder of the shared key could claim one signed "before".
    make_user("ana", "admin")
    _publish_all(cfs)
    assert _me(client, _hs256("ana", iat=0)).status_code == 401


def test_keys_or_a_membership_that_cannot_be_read_never_turn_the_shared_key_back_on(
    client, make_user, cfs, monkeypatch
):
    make_user("ana", "admin")
    _publish_all(cfs)

    def unreadable(name):
        raise peer_auth._Unknown

    monkeypatch.setattr(peer_auth, "_read", unreadable)  # hyperlite-cfs not answering
    assert _me(client, _hs256("ana")).status_code == 401
    monkeypatch.setattr(peer_auth, "_members", lambda: None)
    assert _me(client, _hs256("ana")).status_code == 401


def test_outside_a_cluster_only_those_sessions_remain(client, make_user, cfs, monkeypatch):
    make_user("ana", "admin")
    monkeypatch.setattr(peer_auth, "_mode", lambda: "local")
    assert _me(client, _hs256("ana")).status_code == 401


@pytest.fixture(autouse=True)
def _no_credential_left():
    """A credential remembered by a test stays out of the next one."""
    token = peer_auth.CREDENTIAL.set(None)
    yield
    peer_auth.CREDENTIAL.reset(token)


def test_the_publishing_loop_survives_a_round_that_fails(monkeypatch):
    """At the first start the daemon may run before the API made the database: a round failing on it is logged, and
    the next one publishes. Before, the thread died and the members never got this node's keys."""
    import sqlite3
    import time

    rounds = []

    def publish():
        rounds.append(time.monotonic())
        if len(rounds) == 1:
            raise sqlite3.OperationalError("no such table: app_settings")

    # A failed round is tried again soon, not at the next round.
    monkeypatch.setattr(peer_auth, "PUBLISH_S", 600)
    monkeypatch.setattr(peer_auth, "RETRY_S", 0.01)
    monkeypatch.setattr(peer_auth, "publish", publish)
    monkeypatch.setattr(peer_auth.tickets, "ensure_keys", lambda: None)
    stop = peer_auth.start_publishing()
    deadline = time.monotonic() + 5
    while len(rounds) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert len(rounds) >= 2


def test_keys_the_cluster_refused_are_published_again_soon(monkeypatch):
    """A node that joined publishes as it restarts, while the cluster reloads without the quorum: the refused write is
    tried again within seconds, not at the next round."""
    import time

    rounds = []

    def publish():
        rounds.append(time.monotonic())
        return len(rounds) > 1  # the first one refused

    monkeypatch.setattr(peer_auth, "PUBLISH_S", 600)
    monkeypatch.setattr(peer_auth, "RETRY_S", 0.01)
    monkeypatch.setattr(peer_auth, "publish", publish)
    monkeypatch.setattr(peer_auth.tickets, "ensure_keys", lambda: None)
    stop = peer_auth.start_publishing()
    deadline = time.monotonic() + 5
    while len(rounds) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert len(rounds) >= 2
