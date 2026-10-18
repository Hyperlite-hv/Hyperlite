"""The cluster's status (phase E, app/core/statd.py): every node's VMs read from what each node sent, not asked on each
request; a member without a status asked as before; stale and silent nodes named."""

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
        status("pve-a", [{"nom": "web"}], age_ms=2_000, nodeid=2),
        status("pve-b", [{"nom": "db"}], age_ms=45_000, nodeid=3),
        status("pve-c", [{"nom": "old"}], age_ms=120_000, nodeid=4),
        NodeStatus(node=5, age_ms=1000, data=b"not a status"),
    ]
    table = statd.table()
    assert set(table) == {"pve-a", "pve-b"}
    assert table["pve-a"] == {"vms": [{"nom": "web"}], "age_s": 2.0, "stale": False}
    assert table["pve-b"]["stale"] is True


def test_this_nodes_summary_is_sent_compressed(cfs, monkeypatch):
    monkeypatch.setattr(
        statd, "summary", lambda: {"node": "hv-test", "at": 0, "vms": [{"nom": f"vm{i}"} for i in range(333)]}
    )
    size = statd.publish()
    assert size == len(cfs.put[0]) and size < 3000  # 333 summaries of a name: a few KB once compressed


def test_a_change_made_through_hyperlite_sends_the_status_soon():
    statd._wake.clear()
    inventory_cache.invalidate()
    assert statd._wake.is_set()


@pytest.fixture()
def cluster(cfs, monkeypatch):
    members = [{"name": n, "hostname": f"{n}.lan", "statut": "en_ligne"} for n in ("pve-a", "pve-b")]
    monkeypatch.setattr(cluster_view, "members", lambda: members)
    asked = []

    def forward(node, username, method, path):
        asked.append((node["name"], path))
        if node["name"] == "pve-b-down":
            raise cluster_view.PeerError("down")
        return 200, [{"nom": "from-" + node["name"]}]

    monkeypatch.setattr(cluster_view, "forward", forward)
    conn = types.SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(inventory, "open_conn", lambda node=None: conn)
    monkeypatch.setattr(inventory, "_domain_summaries", lambda c, node: [{"nom": "mine"}])
    return {"members": members, "asked": asked}


def test_every_nodes_vms_come_from_the_status_and_a_member_without_one_is_asked(client, auth_headers, cfs, cluster):
    cfs.statuses = [status("pve-a", [{"nom": "web"}], age_ms=40_000)]
    r = client.get("/vms?cluster=true", headers=auth_headers("ana"))
    assert r.status_code == 200
    body = r.json()
    assert sorted((v["nom"], v["node"]) for v in body["items"]) == [
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

    def no_libvirt(node=None):
        raise AssertionError("this node's VMs must come from its status")

    cfs.statuses = [
        status("hv-test", [{"nom": "mine-from-status"}], age_ms=3_000, nodeid=1),
        status("pve-a", [{"nom": "web"}], nodeid=2),
        status("pve-b", [{"nom": "db"}], nodeid=3),
    ]
    monkeypatch.setattr(inventory, "open_conn", no_libvirt)
    body = client.get("/vms?cluster=true", headers=auth_headers("ana")).json()
    assert ("mine-from-status", "local") in [(v["nom"], v["node"]) for v in body["items"]]
    assert cluster["asked"] == []  # nothing asked of anyone
