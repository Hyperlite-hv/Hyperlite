"""The cluster's status (phase E, app/core/statd.py): every node's VMs read from what each node sent, not asked on each
request; a member without a status asked as before; stale and silent nodes named."""

import threading
import time
import types

import pytest

from app.core import cluster_view, inventory_cache, statd
from app.core.cfs_client import NodeStatus
from app.routers.vms import inventory


class FakeCfs:
    def __init__(self, statuses):
        self.statuses = statuses
        self.put = []

    def stat_get(self):
        return self.statuses

    def stat_put(self, data):
        self.put.append(data)


def status(node, vms, age_ms=1000, nodeid=2):
    return NodeStatus(node=nodeid, age_ms=age_ms, data=statd.encode({"node": node, "at": 0, "vms": vms}))


@pytest.fixture()
def cfs(monkeypatch):
    fake = FakeCfs([])
    monkeypatch.setattr("app.repositories.cfs.shadow._get_client", lambda: fake)
    return fake


def test_the_table_keeps_current_and_stale_statuses_and_drops_gone_or_unreadable_ones(cfs):
    cfs.statuses = [
        status("pve-a", [{"name": "web"}], age_ms=2_000, nodeid=2),
        status("pve-b", [{"name": "db"}], age_ms=45_000, nodeid=3),
        status("pve-c", [{"name": "old"}], age_ms=120_000, nodeid=4),
        NodeStatus(node=5, age_ms=1000, data=b"not a status"),
    ]
    table = statd.table()
    assert set(table) == {"pve-a", "pve-b"}
    assert table["pve-a"] == {
        "vms": [{"name": "web"}],
        "age_s": 2.0,
        "stale": False,
        "api": 0,
        "features": [],
        "mem_free_mb": None,
        "load": None,
    }
    assert table["pve-b"]["stale"] is True


def test_this_nodes_summary_is_sent_compressed(cfs, monkeypatch):
    monkeypatch.setattr(
        statd, "summary", lambda: {"node": "hv-test", "at": 0, "vms": [{"name": f"vm{i}"} for i in range(333)]}
    )
    size = statd.publish()
    assert size == len(cfs.put[0]) and size < 3000  # 333 summaries of a name: a few KB once compressed


def test_a_change_made_through_hyperlite_sends_the_status_soon(monkeypatch):
    monkeypatch.setattr(statd, "_loop_runs", [True])  # this process runs the periodic loop: the change wakes it
    statd._wake.clear()
    inventory_cache.invalidate()
    assert statd._wake.is_set()


def _changes_without_the_loop(monkeypatch, euid):
    """Three changes in a row in a process without the periodic loop (the API on an installed node): what went out."""
    from app.core import privileged

    for thread in threading.enumerate():  # a summary an earlier test asked for goes first, outside this one
        if thread.name == "statd-changed":
            thread.join(5)
    sent = []
    monkeypatch.setattr(statd, "_loop_runs", [False])
    monkeypatch.setattr(statd, "_pending", [False])
    monkeypatch.setattr(statd, "DEBOUNCE_S", 0.05)
    monkeypatch.setattr(statd, "_in_cluster", lambda: True)
    monkeypatch.setattr(statd, "publish", lambda: sent.append(("here", None)))
    monkeypatch.setattr(statd.os, "geteuid", lambda: euid)

    def run(op, **kwargs):
        from app.core import peer_auth

        sent.append((op, peer_auth.CREDENTIAL.get()))

    monkeypatch.setattr(privileged, "run", run)
    from app.core import peer_auth

    token = peer_auth.CREDENTIAL.set("the request's proof")
    try:
        for _ in range(3):
            statd.changed()
    finally:
        peer_auth.CREDENTIAL.reset(token)
    deadline = time.monotonic() + 5
    while not sent and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)  # nothing more comes
    return sent


def test_as_root_the_api_sends_one_summary_for_changes_in_a_row(monkeypatch):
    assert _changes_without_the_loop(monkeypatch, 0) == [("here", None)]


def test_an_api_that_is_not_root_asks_the_daemon_with_the_requests_proof(monkeypatch):
    """hyperlite-cfs takes STAT_PUT from root only: an API as www-data asks hyperlite-daemon, once, on a thread that
    keeps the proof of the request that made the change (tasks.start_thread)."""
    assert _changes_without_the_loop(monkeypatch, 33) == [("statd.publish", "the request's proof")]


def test_the_daemon_sends_at_most_one_summary_a_second(monkeypatch):
    import app.daemon.ops  # noqa: F401  (registers the operations)
    from app.daemon.ops import cluster as ops

    calls = []
    monkeypatch.setattr(statd, "publish", lambda: calls.append(1) or 123)
    monkeypatch.setattr(ops, "_statd_last", [0.0])
    assert ops.statd_publish(None, None) == {"sent": 123}
    assert ops.statd_publish(None, None) == {"sent": None}  # asked again at once: the one just sent is current
    assert calls == [1]


@pytest.fixture()
def cluster(cfs, monkeypatch):
    members = [{"name": n, "hostname": f"{n}.lan", "status": "online"} for n in ("pve-a", "pve-b")]
    monkeypatch.setattr(cluster_view, "members", lambda: members)
    asked = []

    def forward(node, username, method, path):
        asked.append((node["name"], path))
        if node["name"] == "pve-b-down":
            raise cluster_view.PeerError("down")
        return 200, [{"name": "from-" + node["name"]}]

    monkeypatch.setattr(cluster_view, "forward", forward)
    conn = types.SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(inventory, "open_conn", lambda node=None, **_k: conn)
    monkeypatch.setattr(inventory, "_domain_summaries", lambda c, node: [{"name": "mine"}])
    return {"members": members, "asked": asked}


def test_every_nodes_vms_come_from_the_status_and_a_member_without_one_is_asked(client, auth_headers, cfs, cluster):
    cfs.statuses = [status("pve-a", [{"name": "web"}], age_ms=40_000)]
    r = client.get("/vms?cluster=true", headers=auth_headers("ana"))
    assert r.status_code == 200
    body = r.json()
    assert sorted((v["name"], v["node"]) for v in body["items"]) == [
        ("from-pve-b", "pve-b"),  # no status (an older version): asked as before
        ("mine", "local"),
        ("web", "pve-a"),  # from its status: not asked
    ]
    assert cluster["asked"] == [("pve-b", "/vms")]
    assert body["stale"] == ["pve-a"] and body["unreachable"] == []


def test_a_member_without_a_status_that_does_not_answer_is_named(client, auth_headers, cfs, cluster):
    cluster["members"][1]["name"] = "pve-b-down"
    r = client.get("/vms?cluster=true", headers=auth_headers("ana"))
    assert r.status_code == 200 and r.json()["unreachable"] == ["pve-b-down"]


def test_this_nodes_vms_come_from_its_own_status_when_current(client, auth_headers, cfs, cluster, monkeypatch):
    """Antho, 2026-10-18: as Proxmox, this node's VMs are read from its status too; live only without a current one."""

    def no_libvirt(node=None, **_k):
        raise AssertionError("this node's VMs must come from its status")

    cfs.statuses = [
        status("hv-test", [{"name": "mine-from-status"}], age_ms=3_000, nodeid=1),
        status("pve-a", [{"name": "web"}], nodeid=2),
        status("pve-b", [{"name": "db"}], nodeid=3),
    ]
    monkeypatch.setattr(inventory, "open_conn", no_libvirt)
    body = client.get("/vms?cluster=true", headers=auth_headers("ana")).json()
    assert ("mine-from-status", "local") in [(v["name"], v["node"]) for v in body["items"]]
    assert cluster["asked"] == []  # nothing asked of anyone


def test_a_status_carries_the_nodes_and_its_vms_load_as_the_collector_last_measured_it(cfs, monkeypatch):
    # Load balancing reads it from the statuses (docs/design/drs.md, 3.1): measured once, by the collector.
    from app.core import metrics

    load = {"cpu_pct": 42.0, "mem_used_mb": 3000.0, "mem_total_mb": 8000.0}
    vms = {"web": {"cpu_pct": 80.0, "vcpus": 2, "mem_used_mb": 900.0, "mem_total_mb": 1024.0}}
    clock = [1000.0]
    monkeypatch.setattr(metrics.time, "time", lambda: clock[0])
    metrics._record_load(clock[0], load, vms)
    got = metrics.latest_load()
    assert got["host"]["cpu_pct"] == 42.0 and got["host"]["cpus"] >= 1 and got["vms"] == vms
    clock[0] += metrics.LOAD_FRESH_S + 1
    assert metrics.latest_load() is None  # a collector that stopped is no current load
    clock[0] -= metrics.LOAD_FRESH_S + 1
    cfs.statuses = [
        NodeStatus(node=2, age_ms=1000, data=statd.encode({"node": "pve-a", "at": 0, "vms": [], "load": got}))
    ]
    assert statd.table()["pve-a"]["load"] == got
