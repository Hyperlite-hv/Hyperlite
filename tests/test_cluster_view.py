"""The cluster's activity from any node (app/core/cluster_view.py): GET /tasks and GET /audit with cluster=true ask
every other member, merge the answers newest first, mark each row with the node that holds it, and name the members
that did not answer; a task's detail, log and cancellation go to the node that holds it."""

import urllib.parse

import pytest

from app.core import audit, cluster, cluster_lead, cluster_setup, rolling_update
from app.core.tasks import create_task

TASK_A = "0b5e5cf2-6a39-4d55-9f8f-0f8f6f3c1a11"


class Peers:
    """The other members' APIs: what each answers, and every call made."""

    def __init__(self):
        self.calls = []
        self.answers = {}  # (node, path without query) -> (status, body)
        self.down = set()

    def api(self, node, username, method, path, body=None, timeout=60, connect_timeout=10):
        self.calls.append((node["name"], username, method, path))
        if node["name"] in self.down:
            raise rolling_update.PeerUnreachable(f"{node['name']} is not reachable over SSH")
        return self.answers.get((node["name"], path.split("?")[0]), (404, {"detail": "Task not found"}))

    def query(self, node, path):
        for name, _, _, p in self.calls:
            if name == node and p.split("?")[0] == path:
                return dict(urllib.parse.parse_qsl(p.partition("?")[2]))
        return None


def _node(name, statut="en_ligne"):
    return {"name": name, "hostname": f"{name}.lan", "ssh_user": "root", "ssh_port": 22, "statut": statut}


@pytest.fixture()
def peers(database, monkeypatch):
    fake = Peers()
    monkeypatch.setattr(cluster_lead, "mode", lambda record=True: "cluster")
    members = [
        {"nom": n, "nodeid": i, "adresse": f"10.0.0.{i}"} for i, n in enumerate(("hv-test", "pve-a", "pve-b"), 1)
    ]
    monkeypatch.setattr(cluster_setup, "shared_conf", lambda: {"cluster": "lab", "version": 3, "membres": members})
    # pve-old is registered over SSH only (no Hyperlite on it): not a member, never asked.
    nodes = [_node("pve-a"), _node("pve-b"), _node("pve-old")]
    monkeypatch.setattr(cluster, "list_nodes", lambda: nodes)
    monkeypatch.setattr(rolling_update, "peer_api", fake.api)
    fake.nodes = nodes
    return fake


def _task_row(task_id, created, node, cible="web"):
    return {"id": task_id, "type": "backup", "cible": cible, "node": node, "username": "system", "statut": "termine",
            "progres": 100, "cree_le": created, "debut_le": created, "fin_le": created, "erreur": None}  # fmt: skip


def test_outside_a_cluster_the_lists_are_this_nodes_own(client, auth_headers, monkeypatch):
    headers = auth_headers("ana")
    create_task("start_vm", "web", None, "ana")
    r = client.get("/tasks?cluster=true", headers=headers)
    assert r.status_code == 200
    assert [t["on"] for t in r.json()["items"]] == ["hv-test"] and r.json()["unreachable"] == []
    assert isinstance(client.get("/tasks", headers=headers).json(), list)  # the plain list is unchanged


def test_the_tasks_of_every_member_newest_first(client, auth_headers, peers):
    headers = auth_headers("ana")
    mine = create_task("start_vm", "web", None, "ana")
    peers.answers[("pve-a", "/tasks")] = (200, [_task_row(TASK_A, "2999-01-01T00:00:00+00:00", "pve-a")])
    peers.answers[("pve-b", "/tasks")] = (200, [_task_row(TASK_A.replace("a11", "b22"), "2000-01-01T00:00:00+00:00", "pve-b")])  # fmt: skip
    rows = client.get("/tasks?cluster=true&limit=50", headers=headers).json()
    assert [(t["on"], t["id"]) for t in rows["items"]] == [
        ("pve-a", TASK_A), ("hv-test", mine), ("pve-b", TASK_A.replace("a11", "b22"))
    ]  # fmt: skip
    assert rows["unreachable"] == []
    assert {c[0] for c in peers.calls} == {"pve-a", "pve-b"}  # pve-old is not a member
    assert all(c[1] == "ana" for c in peers.calls)  # asked in the name of the user who looks
    assert peers.query("pve-a", "/tasks")["limit"] == "50"
    assert "cluster" not in peers.query("pve-a", "/tasks")  # a member answers with its own tasks only

    rows = client.get("/tasks?cluster=true&limit=2", headers=headers).json()["items"]
    assert len(rows) == 2


def test_a_member_down_or_offline_is_named_and_offline_ones_are_not_asked(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.down.add("pve-a")
    peers.nodes[1]["statut"] = "hors_ligne"
    rows = client.get("/tasks?cluster=true", headers=headers).json()
    assert sorted(rows["unreachable"]) == ["pve-a", "pve-b"]
    assert [c[0] for c in peers.calls] == ["pve-a"]


def test_this_nodes_filter_is_sent_under_its_name(client, auth_headers, peers):
    headers = auth_headers("ana")
    client.get("/tasks?cluster=true&node=local", headers=headers)
    assert peers.query("pve-b", "/tasks")["node"] == "hv-test"


def test_a_task_held_by_another_member_is_read_and_cancelled_there(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.answers[("pve-a", f"/tasks/{TASK_A}")] = (200, _task_row(TASK_A, "2026-10-17T00:00:00+00:00", "pve-a"))
    peers.answers[("pve-a", f"/tasks/{TASK_A}/log")] = (200, [{"at": "x", "message": "copied"}])
    peers.answers[("pve-a", f"/tasks/{TASK_A}/cancel")] = (409, {"detail": "This task has already ended"})
    assert client.get(f"/tasks/{TASK_A}?on=pve-a", headers=headers).json()["id"] == TASK_A
    assert client.get(f"/tasks/{TASK_A}/log?on=pve-a", headers=headers).json()[0]["message"] == "copied"
    r = client.post(f"/tasks/{TASK_A}/cancel?on=pve-a&force=true", headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == "This task has already ended"
    assert ("pve-a", "ana", "POST", f"/tasks/{TASK_A}/cancel?force=true") in peers.calls

    # This node's own name, or none: read here.
    assert client.get(f"/tasks/{TASK_A}?on=hv-test", headers=headers).status_code == 404
    assert len(peers.calls) == 3


def test_what_cannot_reach_another_member(client, auth_headers, peers):
    headers = auth_headers("ana")
    assert client.get(f"/tasks/{TASK_A}?on=pve-old", headers=headers).status_code == 404  # not a member
    assert client.get("/tasks/..%2Fvms?on=pve-a", headers=headers).status_code == 404  # not a task id
    assert peers.calls == []
    # A member that refuses the session: never a 401 here, which would sign the dashboard out.
    peers.answers[("pve-a", f"/tasks/{TASK_A}")] = (401, {"detail": "Could not validate credentials"})
    assert client.get(f"/tasks/{TASK_A}?on=pve-a", headers=headers).status_code == 502
    peers.down.add("pve-a")
    r = client.get(f"/tasks/{TASK_A}?on=pve-a", headers=headers)
    assert r.status_code == 502 and "pve-a" in r.json()["detail"]


def test_the_audit_log_of_every_member(client, auth_headers, peers):
    headers = auth_headers("ana")
    audit._AUDIT_QUEUE.join()  # the sign-in entry is written
    entry = {"id": 9, "timestamp": "2999-01-01T00:00:00+00:00", "username": "bob", "action": "delete_vm",
             "resource": "web", "result": "succes", "error_message": None, "ip": None}  # fmt: skip
    peers.answers[("pve-a", "/audit")] = (200, [entry])
    peers.answers[("pve-a", "/audit/count")] = (200, {"total": 41})
    peers.answers[("pve-a", "/audit/actions")] = (200, ["delete_vm"])
    peers.down.add("pve-b")
    rows = client.get("/audit?cluster=true", headers=headers).json()
    assert rows["items"][0] == {**entry, "on": "pve-a"} and rows["unreachable"] == ["pve-b"]
    assert all(r["on"] == "hv-test" for r in rows["items"][1:])
    local = client.get("/audit/count", headers=headers).json()["total"]
    assert client.get("/audit/count?cluster=true", headers=headers).json()["total"] == local + 41
    assert "delete_vm" in client.get("/audit/actions?cluster=true", headers=headers).json()
    assert isinstance(client.get("/audit", headers=headers).json(), list)  # the plain list is unchanged
