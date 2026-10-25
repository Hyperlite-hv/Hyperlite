"""Several clusters from one view (docs/design/multi-cluster.md, step 1): a remote recorded from one of its nodes, its
certificate pinned and a viewer token kept sealed; the view asks every remote at once and names those that do not
answer. Both sides are this test's application: the pinned HTTPS connection is replaced by one to the test client."""

import json

import pytest

from app.core import peer_https, self_node, sites, statd
from app.core.api_tokens import create_token

PIN = "ab" * 32


class _Response:
    def __init__(self, r):
        self.status, self._body = r.status_code, r.content

    def read(self):
        return self._body


class _Canned:
    def __init__(self, status, body):
        self.status_code, self.content = status, json.dumps(body).encode()


class _Connection:
    """What peer_https._connect gives, answered by the test client (or by `canned`, {(method, path): (status, body)},
    for what the test backend cannot do: a VM that runs)."""

    def __init__(self, client, state, address):
        self.client, self.state, self.address, self.sock, self._last = client, state, address, self, None

    def settimeout(self, _):
        pass

    def request(self, method, path, body=None, headers=None):
        path = path.removeprefix(sites.API)
        self.state["requests"].append((self.address, method, path, headers["Authorization"], body))
        if (method, path) in self.state["canned"]:
            self._last = _Canned(*self.state["canned"][(method, path)])
            return
        self._last = self.client.request(method, path.lstrip("/"), content=body, headers=headers)

    def getresponse(self):
        return _Response(self._last)

    def close(self):
        pass


@pytest.fixture()
def wire(client, monkeypatch):
    """The remote's nodes: every address answers through the test client but those in `down`; the calls are kept."""
    state = {"calls": [], "down": set(), "requests": [], "canned": {}}

    def connect(address, port, fingerprint, timeout):
        state["calls"].append((address, fingerprint))
        if address in state["down"]:
            raise peer_https.Unavailable(f"{address} does not answer")
        return _Connection(client, state, address)

    monkeypatch.setattr(peer_https, "_connect", connect)
    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "cd" * 32)
    sites.forget_cache()
    return state


@pytest.fixture()
def viewer(make_user):
    """The remote's dedicated account: a global viewer, and its token."""
    make_user("watcher", "viewer")
    return create_token("watcher", "view")[1]


@pytest.fixture()
def load(monkeypatch):
    """This node's status, as the status channel gives it."""
    me = self_node.name()
    vms = [{"name": "web", "state": "active"}, {"name": "db", "state": "crashed"}, {"name": "old", "state": "shutoff"}]
    host = {"mem_total_mb": 64000, "cpu_pct": 12.5, "cpus": 16}
    monkeypatch.setattr(
        statd,
        "table",
        lambda: {me: {"vms": vms, "age_s": 1.0, "stale": False, "mem_free_mb": 32000, "load": {"host": host}}},
    )
    return me


def test_the_summary_tells_a_view_this_cluster_at_a_glance(client, viewer, load):
    summary = client.get("/sites/summary", headers={"Authorization": f"Bearer {viewer}"}).json()
    assert summary["node"] == load and summary["vms"] == {"running": 1, "stopped": 2, "crashed": 1}
    [node] = summary["nodes"]
    assert node["online"] and node["vms_running"] == 1 and node["vms_total"] == 3 and node["cpus"] == 16
    assert "1 VM(s) crashed" in summary["problems"]
    assert client.get("/sites/summary").status_code == 401


def test_a_remote_is_added_and_viewed(client, auth_headers, viewer, load, wire):
    admin = auth_headers("ana")
    body = {"name": "site-b", "address": "10.0.0.7", "port": 8000, "fingerprint": PIN.upper(), "view_token": viewer}
    r = client.post("/sites/remotes", json=body, headers=admin)
    assert r.status_code == 201, r.text
    remote = r.json()
    assert remote["uses"] == ["view"] and remote["endpoints"][0] == {
        "address": "10.0.0.7",
        "port": 8000,
        "fingerprint": PIN,
        "node": load,
    }
    assert viewer not in r.text and all(fingerprint == PIN for _, fingerprint in wire["calls"])
    assert client.get("/sites/remotes", headers=admin).json()["items"][0]["name"] == "site-b"
    [own, other] = client.get("/sites", headers=admin).json()["items"]
    assert own["self"] and not other["self"] and other["reachable"]
    assert other["summary"]["vms"]["crashed"] == 1
    assert client.get("/sites/site-b/nodes", headers=admin).status_code == 200
    assert client.get("/sites/site-b/secrets", headers=admin).status_code == 404
    assert client.get("/sites/nowhere/vms", headers=admin).status_code == 404
    # A viewer of this cluster does not see the others.
    assert client.get("/sites", headers={"Authorization": f"Bearer {viewer}"}).status_code == 403
    assert client.delete("/sites/remotes/site-b", headers=admin).json() == {"deleted": "site-b"}
    assert client.get("/sites/remotes", headers=admin).json()["items"] == []


def test_a_remote_is_saved_only_with_a_token_it_accepts(viewer, load, wire):
    with pytest.raises(sites.SiteError, match="refused the token") as e:
        sites.add("bad", "10.0.0.7", 8000, PIN, "not-a-token", "ana")
    assert e.value.status == 403
    for address, port, fingerprint, message in (
        ("bad host!", 8000, PIN, "Invalid address"),
        ("10.0.0.7", 0, PIN, "Invalid port"),
        ("10.0.0.7", 8000, "abc", "64 hexadecimal"),
    ):
        with pytest.raises(sites.SiteError, match=message):
            sites.add("x", address, port, fingerprint, viewer, "ana")
    with pytest.raises(sites.SiteError, match="Invalid name"):
        sites.add("../x", "10.0.0.7", 8000, PIN, viewer, "ana")
    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    with pytest.raises(sites.SiteError, match="already exists"):
        sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")


def test_a_remote_that_does_not_answer_is_named_not_waited_for(viewer, load, wire):
    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    wire["down"].add("10.0.0.7")
    sites.forget_cache()
    [_, other] = sites.overview()
    assert not other["reachable"] and "does not answer" in other["problem"]
    with pytest.raises(sites.SiteError, match="at least 2"):
        sites.search("w")
    assert sites.search("web")["unreachable"] == ["site-b"]


def test_every_member_is_an_endpoint_and_the_next_answers_when_one_does_not(viewer, load, wire, monkeypatch):
    from app.core import cluster_lead, cluster_setup

    members = [
        {"name": load, "address": "10.0.0.7"},
        {"name": "n2", "address": "10.0.0.8"},
        {"name": "n3", "address": "x y"},
    ]
    monkeypatch.setattr(cluster_lead, "mode", lambda record=True: "cluster")
    monkeypatch.setattr(cluster_setup, "shared_conf", lambda: {"cluster": "lyon", "members": members})
    monkeypatch.setattr(peer_https, "fingerprint_of", lambda name: {"n2": "ef" * 32, "n3": "01" * 32}.get(name))
    remote = sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    # The member with an address that is not one is left out; each member's certificate is its own.
    assert [(e["address"], e["fingerprint"], e["node"]) for e in remote["endpoints"]] == [
        ("10.0.0.7", PIN, load),
        ("10.0.0.8", "ef" * 32, "n2"),
    ]
    summary = sites.call("site-b", "/sites/summary")
    assert summary["cluster"] == "lyon" and "n2 does not answer" in summary["problems"]
    wire["down"].add("10.0.0.7")
    wire["calls"].clear()
    assert sites.call("site-b", "/sites/summary")["node"] == load
    assert wire["calls"] == [("10.0.0.7", PIN), ("10.0.0.8", "ef" * 32)]
    # The cache: a page opened twice asks once.
    sites.forget_cache()
    wire["calls"].clear()
    sites._cached("site-b", "/sites/summary")
    sites._cached("site-b", "/sites/summary")
    assert len(wire["calls"]) == 2  # the endpoint down, then the one that answers, once


def test_the_tokens_are_replicated_as_secrets():
    from app.repositories.cfs import tables

    assert tables.TABLES["remote_tokens"].get("priv") and not tables.TABLES["remotes"].get("priv")


@pytest.fixture()
def manager(make_user):
    """The remote's account for the actions, and its token."""
    make_user("operator", "admin")
    return create_token("operator", "manage")[1]


def _two_nodes(me, viewer):
    """A remote of two members, as sites.add records it from a cluster (the test backend is not one)."""
    from app.core import secrets_crypto

    sites._repo().save_remote("site-b", "ana", "2026-10-25T00:00:00+00:00", [
        {"address": "10.0.0.7", "port": 8000, "fingerprint": PIN, "node": me},
        {"address": "10.0.0.8", "port": 8000, "fingerprint": "ef" * 32, "node": "n2"},
    ])  # fmt: skip
    sites._repo().save_token("site-b", "view", secrets_crypto.encrypt(viewer))


def test_an_action_goes_to_the_node_of_the_vm_with_the_manage_token(
    client, auth_headers, viewer, manager, load, wire, monkeypatch
):
    admin = auth_headers("ana")
    _two_nodes(load, viewer)
    # No manage token yet: nothing to act with.
    r = client.post("/sites/site-b/vms/web/start", json={"on": "n2"}, headers=admin)
    assert r.status_code == 409 and "No manage token" in r.json()["detail"]
    r = client.put("/sites/remotes/site-b/tokens/manage", json={"token": manager}, headers=admin)
    assert r.status_code == 200 and r.json()["uses"] == ["manage", "view"]
    wire["canned"][("POST", "/vms/web/start")] = (200, {"task_id": "t1"})
    wire["canned"][("POST", "/vms/web/stop?force=true&node=n2")] = (200, {"task_id": "t2"})
    wire["requests"].clear()
    # The VM runs on n2: n2's own endpoint first, with no node.
    assert client.post("/sites/site-b/vms/web/start", json={"on": "n2"}, headers=admin).json() == {"task_id": "t1"}
    assert wire["requests"][-1][:4] == ("10.0.0.8", "POST", "/vms/web/start", f"Bearer {manager}")
    # n2 does not answer: the other member, which forwards it there (?node=n2).
    wire["down"].add("10.0.0.8")
    assert client.post("/sites/site-b/vms/web/stop", json={"on": "n2"}, headers=admin).json() == {"task_id": "t2"}
    assert wire["requests"][-1][:3] == ("10.0.0.7", "POST", "/vms/web/stop?force=true&node=n2")
    assert client.post("/sites/site-b/vms/web/explode", json={}, headers=admin).status_code == 404


def test_the_remote_refuses_with_its_own_rights_and_its_answer_is_passed_on(client, auth_headers, viewer, load, wire):
    from app.core import secrets_crypto

    admin = auth_headers("ana")
    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    # A manage token that is in fact a viewer's: the remote refuses the action itself.
    sites._repo().save_token("site-b", "manage", secrets_crypto.encrypt(viewer))
    r = client.post("/sites/site-b/vms/web/start", json={}, headers=admin)
    assert r.status_code == 403 and "refused the token" in r.json()["detail"]
    wire["canned"][("POST", "/vms/web/migrate")] = (409, {"detail": "web is locked by a backup"})
    sites._repo().save_token("site-b", "manage", secrets_crypto.encrypt("x"))
    wire["requests"].clear()
    r = client.post("/sites/site-b/vms/web/migrate", json={"target_node": "n2"}, headers=admin)
    assert r.status_code == 409 and "locked by a backup" in r.json()["detail"]
    assert json.loads(wire["requests"][-1][4]) == {"target_node": "n2"}


def test_an_action_that_reached_the_remote_is_not_sent_again(viewer, load, wire, monkeypatch):
    from app.core import secrets_crypto

    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    sites._repo().delete_remote("site-b")
    sites._repo().save_remote("site-b", "ana", "2026-10-25T00:00:00+00:00", [
        {"address": "10.0.0.7", "port": 8000, "fingerprint": PIN, "node": "n1"},
        {"address": "10.0.0.8", "port": 8000, "fingerprint": PIN, "node": "n2"},
    ])  # fmt: skip
    sites._repo().save_token("site-b", "manage", secrets_crypto.encrypt(viewer))
    wire["canned"][("POST", "/vms/web/restart")] = (500, {"detail": "it broke halfway"})
    wire["requests"].clear()
    with pytest.raises(sites.SiteError, match="answered 500") as e:
        sites.act("site-b", "web", "reboot", on="n1")
    assert e.value.status == 502 and len(wire["requests"]) == 1


def test_tokens_are_checked_before_they_are_kept(client, auth_headers, viewer, load, wire):
    admin = auth_headers("ana")
    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    r = client.put("/sites/remotes/site-b/tokens/manage", json={"token": "nope"}, headers=admin)
    assert r.status_code == 403
    assert client.put("/sites/remotes/site-b/tokens/root", json={"token": viewer}, headers=admin).status_code == 404
    assert client.delete("/sites/remotes/site-b/tokens/view", headers=admin).status_code == 409
    client.put("/sites/remotes/site-b/tokens/manage", json={"token": viewer}, headers=admin)
    assert client.delete("/sites/remotes/site-b/tokens/manage", headers=admin).json()["uses"] == ["view"]
    with pytest.raises(sites.SiteError, match="refused the token"):
        sites.add("site-c", "10.0.0.7", 8000, PIN, viewer, "ana", manage_token="nope")
    assert sites._repo().get_remote("site-c") is None
