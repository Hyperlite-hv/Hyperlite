"""The cluster's activity from any node (app/core/cluster_view.py): GET /tasks and GET /audit with cluster=true ask
every other member, merge the answers newest first, mark each row with the node that holds it, and name the members
that did not answer; a task's detail, log and cancellation go to the node that holds it."""

import urllib.parse

import pytest

from app.core import audit, cluster, cluster_setup, cluster_view, rolling_update
from app.core.tasks import create_task

TASK_A = "0b5e5cf2-6a39-4d55-9f8f-0f8f6f3c1a11"


class Peers:
    """The other members' APIs: what each answers, and every call made."""

    def __init__(self):
        self.calls = []
        self.bodies = []
        self.answers = {}  # (node, path without query) -> (status, body)
        self.down = set()

    def api(self, node, username, method, path, body=None, timeout=60, connect_timeout=10):
        self.calls.append((node["name"], username, method, path))
        self.bodies.append(body)
        if node["name"] in self.down:
            raise rolling_update.PeerUnreachable(f"{node['name']} is not reachable over SSH")
        return self.answers.get((node["name"], path.split("?")[0]), (404, {"detail": "Task not found"}))

    def query(self, node, path):
        for name, _, _, p in self.calls:
            if name == node and p.split("?")[0] == path:
                return dict(urllib.parse.parse_qsl(p.partition("?")[2]))
        return None


def v1(rows):
    """A member's list, as 1.0 answers it (app/core/listing.py)."""
    return {"items": rows, "total": len(rows)}


def _node(name, status="online"):
    return {"name": name, "hostname": f"{name}.lan", "ssh_user": "root", "ssh_port": 22, "status": status}


@pytest.fixture()
def peers(database, monkeypatch):
    fake = Peers()
    # Only this view sees a cluster: the rest of the application (the ids of new rows) stays on a single node.
    monkeypatch.setattr(cluster_view, "_mode", lambda: "cluster")
    members = [
        {"name": n, "nodeid": i, "address": f"10.0.0.{i}"} for i, n in enumerate(("hv-test", "pve-a", "pve-b"), 1)
    ]
    monkeypatch.setattr(cluster_setup, "shared_conf", lambda: {"cluster": "lab", "version": 3, "members": members})
    # pve-old is registered over SSH only (no Hyperlite on it): not a member, never asked.
    nodes = [_node("pve-a"), _node("pve-b"), _node("pve-old")]
    monkeypatch.setattr(cluster, "list_nodes", lambda: nodes)
    monkeypatch.setattr(rolling_update, "peer_api", fake.api)
    fake.nodes = nodes
    return fake


def _task_row(task_id, created, node, target="web"):
    return {"id": task_id, "type": "backup", "target": target, "node": node, "username": "system", "status": "succeeded",
            "progress": 100, "created_at": created, "started_at": created, "finished_at": created, "error": None}  # fmt: skip


def test_outside_a_cluster_the_lists_are_this_nodes_own(client, auth_headers, monkeypatch):
    headers = auth_headers("ana")
    create_task("start_vm", "web", None, "ana")
    r = client.get("/tasks?cluster=true", headers=headers)
    assert r.status_code == 200
    assert [t["on"] for t in r.json()["items"]] == ["hv-test"] and r.json()["unreachable"] == []
    assert isinstance(client.get("/tasks", headers=headers).json()["items"], list)  # the plain list is unchanged


def test_the_tasks_of_every_member_newest_first(client, auth_headers, peers):
    headers = auth_headers("ana")
    mine = create_task("start_vm", "web", None, "ana")
    peers.answers[("pve-a", "/tasks")] = (200, v1([_task_row(TASK_A, "2999-01-01T00:00:00+00:00", "pve-a")]))
    peers.answers[("pve-b", "/tasks")] = (200, v1([_task_row(TASK_A.replace("a11", "b22"), "2000-01-01T00:00:00+00:00", "pve-b")]))  # fmt: skip
    rows = client.get("/tasks?cluster=true&limit=50", headers=headers).json()
    assert [(t["on"], t["id"]) for t in rows["items"]] == [
        ("pve-a", TASK_A), ("hv-test", mine), ("pve-b", TASK_A.replace("a11", "b22"))
    ]  # fmt: skip
    assert rows["unreachable"] == []
    assert {c[0] for c in peers.calls} == {"pve-a", "pve-b"}  # pve-old is not a member
    assert all(c[1] == "ana" for c in peers.calls)  # asked in the name of the user who looks
    assert peers.query("pve-a", "/tasks")["limit"] == "50"
    assert "cluster" not in peers.query("pve-a", "/tasks")  # a member answers with its own tasks only

    rows = client.get("/tasks?cluster=true&limit=2", headers=headers).json()
    assert len(rows["items"]) == 2  # it counted the answer's keys, two before "total", and passed whatever the limit


def test_a_member_down_or_offline_is_named_and_offline_ones_are_not_asked(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.down.add("pve-a")
    peers.nodes[1]["status"] = "offline"
    rows = client.get("/tasks?cluster=true", headers=headers).json()
    assert sorted(rows["unreachable"]) == ["pve-a", "pve-b"]
    assert [c[0] for c in peers.calls] == ["pve-a"]


def test_a_member_marked_offline_that_still_sends_its_status_is_asked(client, auth_headers, peers, monkeypatch):
    """Lab, 2026-10-18: after a network cut a member back on line stayed marked offline until the next poll (61 s), and
    the views skipped it. Its status (phase E, app/core/statd.py) says it is back."""
    from app.core import statd

    headers = auth_headers("ana")
    peers.nodes[0]["status"] = peers.nodes[1]["status"] = "offline"
    peers.answers[("pve-a", "/tasks")] = (200, v1([]))
    monkeypatch.setattr(
        statd,
        "table",
        lambda: {
            "pve-a": {"vms": [], "age_s": 3.0, "stale": False},
            "pve-b": {"vms": [], "age_s": 40.0, "stale": True},
        },
    )
    rows = client.get("/tasks?cluster=true", headers=headers).json()
    assert [c[0] for c in peers.calls] == ["pve-a"]  # pve-b's status is old: still not asked
    assert rows["unreachable"] == ["pve-b"]


def test_this_nodes_filter_is_sent_under_its_name(client, auth_headers, peers):
    headers = auth_headers("ana")
    client.get("/tasks?cluster=true&node=local", headers=headers)
    assert peers.query("pve-b", "/tasks")["node"] == "hv-test"


def test_a_task_held_by_another_member_is_read_and_cancelled_there(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.answers[("pve-a", f"/tasks/{TASK_A}")] = (200, _task_row(TASK_A, "2026-10-17T00:00:00+00:00", "pve-a"))
    peers.answers[("pve-a", f"/tasks/{TASK_A}/log")] = (200, v1([{"at": "x", "message": "copied"}]))
    peers.answers[("pve-a", f"/tasks/{TASK_A}/cancel")] = (409, {"detail": "This task has already ended"})
    assert client.get(f"/tasks/{TASK_A}?on=pve-a", headers=headers).json()["id"] == TASK_A
    assert client.get(f"/tasks/{TASK_A}/log?on=pve-a", headers=headers).json()["items"][0]["message"] == "copied"
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
             "resource": "web", "result": "success", "error_message": None, "ip": None}  # fmt: skip
    peers.answers[("pve-a", "/audit")] = (200, v1([entry]))
    peers.answers[("pve-a", "/audit/count")] = (200, {"total": 41})
    peers.answers[("pve-a", "/audit/actions")] = (200, v1(["delete_vm"]))
    peers.down.add("pve-b")
    rows = client.get("/audit?cluster=true", headers=headers).json()
    assert rows["items"][0] == {**entry, "on": "pve-a"} and rows["unreachable"] == ["pve-b"]
    assert all(r["on"] == "hv-test" for r in rows["items"][1:])
    local = client.get("/audit/count", headers=headers).json()["total"]
    assert client.get("/audit/count?cluster=true", headers=headers).json()["total"] == local + 41
    assert "delete_vm" in client.get("/audit/actions?cluster=true", headers=headers).json()["items"]
    assert isinstance(client.get("/audit", headers=headers).json()["items"], list)  # the plain list is unchanged


# ---- Backups: kept by the node that hosts the VM, acted on there ----


def test_a_backup_of_another_members_vm_is_made_there(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.answers[("pve-a", "/vms/web/backups")] = (202, {"message": "Backup of 'web' started in the background"})
    r = client.post("/vms/web/backups?node=pve-a", json={"target_dir": None}, headers=headers)
    assert r.status_code == 202 and "started" in r.json()["message"]
    assert peers.calls == [("pve-a", "ana", "POST", "/vms/web/backups")]
    assert peers.bodies == [{"target_dir": None}]

    peers.answers[("pve-a", "/vms/web/backups")] = (200, v1([{"id": 3, "vm_name": "web"}]))
    assert client.get("/vms/web/backups?node=pve-a", headers=headers).json()["items"] == [{"id": 3, "vm_name": "web"}]


def test_a_backup_held_by_another_member_is_restored_verified_and_deleted_there(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.answers[("pve-a", "/backups/3/restore")] = (202, {"message": "Restore started in the background"})
    peers.answers[("pve-a", "/backups/3/verify")] = (202, {"task_id": TASK_A, "backup": 3})
    peers.answers[("pve-a", "/backups/3")] = (409, {"detail": "A restore of this backup is running"})
    r = client.post("/backups/3/restore?on=pve-a", json={"mode": "new", "new_name": "web2"}, headers=headers)
    assert r.status_code == 202
    assert peers.bodies[-1] == {"mode": "new", "new_name": "web2"}
    assert client.post("/backups/3/verify?on=pve-a", headers=headers).json()["task_id"] == TASK_A
    r = client.delete("/backups/3?on=pve-a&confirm=true", headers=headers)
    assert r.status_code == 409 and "restore" in r.json()["detail"]
    assert peers.calls[-1] == ("pve-a", "ana", "DELETE", "/backups/3?confirm=true")


def test_what_is_checked_here_before_anything_is_sent(client, auth_headers, peers):
    headers = auth_headers("ana")
    # A node registered over SSH only: refused, never backed up here under the same name.
    r = client.post("/vms/web/backups?node=pve-old", json={}, headers=headers)
    assert r.status_code == 409 and "pve-old is not a member" in r.json()["detail"]
    # Rights are checked here first: an observer cannot delete another member's backup.
    viewer = auth_headers("olga", "viewer")
    assert client.delete("/backups/3?on=pve-a&confirm=true", headers=viewer).status_code == 403
    assert peers.calls == []
    # A member that refuses the session, or does not answer: 502, never a 401.
    peers.answers[("pve-a", "/vms/web/backups")] = (401, {"detail": "Could not validate credentials"})
    assert client.get("/vms/web/backups?node=pve-a", headers=headers).status_code == 502
    peers.down.add("pve-a")
    r = client.get("/vms/web/backups?node=pve-a", headers=headers)
    assert r.status_code == 502 and "pve-a" in r.json()["detail"]
    # A holder that is not a member (it left the cluster): refused, never this node's backup with the same id.
    r = client.delete("/backups/3?on=pve-old&confirm=true", headers=headers)
    assert r.status_code == 409 and "pve-old is not a member" in r.json()["detail"]
    assert client.post("/backups/3/restore?on=gone", json={"mode": "overwrite"}, headers=headers).status_code == 409
    # Browsing another member's backup is done from its own dashboard (downloads are files).
    r = client.post("/backups/3/files?on=pve-b", headers=headers)
    assert r.status_code == 409 and "pve-b" in r.json()["detail"]


def test_every_members_backups_in_one_list(client, auth_headers, peers):
    headers = auth_headers("ana")
    peers.answers[("pve-a", "/backups")] = (200, v1([{"id": 3, "vm_name": "web", "created_at": "2999-01-01T00:00:00"}]))
    peers.down.add("pve-b")
    r = client.get("/backups?cluster=true", headers=headers).json()
    assert r["items"] == [{"id": 3, "vm_name": "web", "created_at": "2999-01-01T00:00:00", "on": "pve-a"}]
    assert r["unreachable"] == ["pve-b"]
    assert client.get("/vms/web/backups?node=local", headers=headers).json()["items"] == []  # this node: read here


def test_the_node_list_says_which_nodes_are_members(client, auth_headers, peers, monkeypatch):
    from app.services import node_service

    async def listed():
        return [{**n, "live": None} for n in peers.nodes]

    monkeypatch.setattr(node_service, "list_with_live", listed)
    nodes = client.get("/nodes", headers=auth_headers("ana")).json()["items"]
    assert {n["name"]: n["member"] for n in nodes} == {"pve-a": True, "pve-b": True, "pve-old": False}


def test_outside_a_cluster_no_node_is_a_member(client, auth_headers, monkeypatch):
    from app.services import node_service

    async def listed():
        return [{**_node("nico"), "live": None}]

    monkeypatch.setattr(node_service, "list_with_live", listed)
    assert client.get("/nodes", headers=auth_headers("ana")).json()["items"][0]["member"] is False
    # This node's own name, as the backups list marks its rows: handled here (no such backup).
    headers = auth_headers("carl")
    assert client.delete("/backups/99?on=hv-test&confirm=true", headers=headers).status_code == 404
    assert client.get("/backups?cluster=true", headers=headers).json() == {"items": [], "total": 0, "unreachable": []}
    # A backup of that node's VM is refused, never made on this host under the same name.
    r = client.post("/vms/web/backups?node=nico", json={}, headers=auth_headers("bob"))
    assert r.status_code == 409 and "nico is not a member" in r.json()["detail"]


def test_another_members_vms_come_from_it_in_one_call(client, auth_headers, peers, monkeypatch):
    """docs/scale-results.md: read through libvirt over SSH they cost one round trip per VM (7 s for 333 VMs over
    Tailscale); the member answers from its own inventory cache."""
    from app.routers.vms import inventory

    def no_libvirt(node=None, **_k):
        raise AssertionError("another member's VMs must not be read through libvirt here")

    monkeypatch.setattr(inventory, "open_conn", no_libvirt)
    peers.answers[("pve-a", "/vms")] = (200, v1([{"name": "web", "state": "active"}]))
    r = client.get("/vms?node=pve-a", headers=auth_headers("ana"))
    assert r.status_code == 200 and r.json()["items"] == [{"name": "web", "state": "active"}]
    assert peers.calls == [("pve-a", "ana", "GET", "/vms")]


def test_a_node_that_does_not_answer_is_named_not_an_error_500(client, auth_headers, peers, monkeypatch):
    import libvirt

    from app.routers.vms import inventory

    def unreachable(node=None, **_k):
        raise libvirt.libvirtError("Cannot recv data: kex_exchange_identification: Connection reset by peer")

    monkeypatch.setattr(inventory, "open_conn", unreachable)
    headers = auth_headers("ana")
    r = client.get("/vms?node=pve-old", headers=headers)  # registered over SSH only: read via libvirt
    assert r.status_code == 502 and "pve-old does not answer" in r.json()["detail"]
    peers.down.add("pve-a")
    r = client.get("/vms?node=pve-a", headers=headers)
    assert r.status_code == 502 and "pve-a does not answer" in r.json()["detail"]


def test_a_member_whose_api_is_not_reached_says_why():
    from app.core import cluster_view

    status, body = cluster_view.answer(0, {"detail": "<urlopen error [Errno 111] Connection refused>"}, "pve-a")
    assert status == 502 and body["detail"] == "pve-a does not answer: <urlopen error [Errno 111] Connection refused>"
    assert cluster_view.answer(0, None, "pve-a") == (502, {"detail": "pve-a does not answer"})
