"""Several clusters from one view (docs/design/multi-cluster.md, step 1): a remote recorded from one of its nodes, its
certificate pinned and a viewer token kept sealed; the view asks every remote at once and names those that do not
answer. Both sides are this test's application: the pinned HTTPS connection is replaced by one to the test client."""

import pytest

from app.core import peer_https, self_node, sites, statd
from app.core.api_tokens import create_token

PIN = "ab" * 32


class _Response:
    def __init__(self, r):
        self.status, self._body = r.status_code, r.content

    def read(self):
        return self._body


class _Connection:
    """What peer_https._connect gives, answered by the test client."""

    def __init__(self, client):
        self.client, self.sock, self._last = client, self, None

    def settimeout(self, _):
        pass

    def request(self, method, path, headers):
        self._last = self.client.request(method, path.removeprefix(sites.API).lstrip("/"), headers=headers)

    def getresponse(self):
        return _Response(self._last)

    def close(self):
        pass


@pytest.fixture()
def wire(client, monkeypatch):
    """The remote's nodes: every address answers through the test client but those in `down`; the calls are kept."""
    state = {"calls": [], "down": set()}

    def connect(address, port, fingerprint, timeout):
        state["calls"].append((address, fingerprint))
        if address in state["down"]:
            raise peer_https.Unavailable(f"{address} does not answer")
        return _Connection(client)

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
