"""Rolling update of a cluster (docs/design/updates-1.0.md, section 9): the other nodes one after another, each
emptied first, this node last; a failure stops the run on its node, which stays in maintenance."""

import contextlib
import json
import subprocess

import pytest

from app.core import maintenance, rolling_update, self_node
from app.core.tasks import create_task

NODES = [
    {"name": "pve2", "hostname": "10.0.0.2", "ssh_user": "root", "ssh_port": 22, "statut": "en_ligne"},
    {"name": "pve3", "hostname": "10.0.0.3", "ssh_user": "root", "ssh_port": 22, "statut": "en_ligne"},
]


class FakeCluster:
    """The other nodes' APIs: each update succeeds (or fails for `failing`), a reboot is needed on `reboot`."""

    def __init__(self, failing=(), reboot=(), blocked=()):
        self.failing, self.reboot, self.blocked = set(failing), set(reboot), set(blocked)
        self.calls, self.drained, self.rebooted = [], [], []
        self.version = {n["name"]: "1.0.0" for n in NODES}

    def api(self, node, username, method, path, body=None, timeout=60):
        name = node["name"]
        self.calls.append((name, method, path))
        assert maintenance.get(name), f"{name} is updated while out of maintenance"
        if (method, path) == ("POST", "/update/apply"):
            return 202, {"task_id": f"t-{name}"}
        if path.startswith("/tasks/"):
            if name in self.failing:
                return 200, {"statut": "echec", "erreur": "apt-get full-upgrade exited with 100"}
            self.version[name] = "1.0.1"
            return 200, {"statut": "termine"}
        if path == "/update/check":
            pending = name in self.reboot and name not in self.rebooted
            return 200, {"commit_local": self.version[name], "systeme": {"redemarrage_requis": pending}}
        if path == "/host/system/power":
            assert body["arreter_invites"] is True and body["confirmation"] == f"host-{name}"
            self.rebooted.append(name)
            return 202, {"tache": "x"}
        if path == "/update/channel":
            return 200, {"canal": "stable"}
        raise AssertionError(f"unexpected call {method} {path}")

    def drain(self, task_id, username, label, target):
        maintenance.enter(label, username)
        self.drained.append((label, target))
        return {"migrables": ["web"], "non_migrables": ["db"] if label in self.blocked else []}


@pytest.fixture()
def cluster(database, monkeypatch):
    def make(**kw):
        fake = FakeCluster(**kw)
        monkeypatch.setattr(rolling_update, "peers", lambda: [dict(n) for n in NODES])
        monkeypatch.setattr(rolling_update, "peer_api", fake.api)
        monkeypatch.setattr(rolling_update, "_drain", fake.drain)
        monkeypatch.setattr(rolling_update, "_LockKeeper", contextlib.nullcontext)
        monkeypatch.setattr(rolling_update, "POLL_S", 0)
        monkeypatch.setattr(self_node, "name", lambda: "pve1")
        boots = {}

        def ssh(node, args, stdin=None, timeout=60):
            if args == ["hostname"]:
                return subprocess.CompletedProcess(args, 0, f"host-{node['name']}\n", "")
            boot = "after" if node["name"] in fake.rebooted else "before"
            boots[node["name"]] = boot
            return subprocess.CompletedProcess(args, 0, boot + "\n", "")

        monkeypatch.setattr(rolling_update, "_ssh", ssh)
        return fake

    return make


def _run(database, local_tasks, restarts=False):
    task_id = create_task("cluster_update", "cluster", username="alice")

    def start_local(username):
        tid = create_task("hyperlite_update", "hyperlite", username=username)
        from app.core.tasks import finish_task

        finish_task(tid, "termine")
        local_tasks.append(tid)
        return tid

    rolling_update.run(task_id, "alice", False, start_local, lambda: restarts)
    with database.get_conn() as conn:
        row = conn.execute("SELECT statut, erreur FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return row["statut"], row["erreur"]


def test_the_other_nodes_one_after_another_then_this_node(cluster, database):
    fake = cluster(reboot={"pve3"})
    local = []
    assert _run(database, local) == ("termine", None)
    # pve2's VMs go to pve3 (nothing done yet), pve3's to pve2 (done), this node's to pve3 (done last).
    assert fake.drained == [("pve2", "pve3"), ("pve3", "pve2"), (maintenance.LOCAL, "pve3")]
    applies = [n for n, m, p in fake.calls if (m, p) == ("POST", "/update/apply")]
    assert applies == ["pve2", "pve3"] and len(local) == 1
    assert fake.rebooted == ["pve3"]
    # Every node out of maintenance: the others by the run, this one once its own update ended without a restart.
    assert maintenance.list_all() == []


def test_a_failure_stops_the_run_on_its_node(cluster, database):
    fake = cluster(failing={"pve2"})
    local = []
    status, error = _run(database, local)
    assert status == "echec" and "pve2: the update failed" in error
    assert [n for n, m, p in fake.calls if (m, p) == ("POST", "/update/apply")] == ["pve2"]
    assert local == []  # neither pve3 nor this node
    assert [m["node"] for m in maintenance.list_all()] == ["pve2"]  # it stays in maintenance


def test_a_node_whose_vms_cannot_move_is_not_rebooted_unless_chosen(cluster, database, monkeypatch):
    fake = cluster(reboot={"pve2"}, blocked={"pve2"})
    logged = []
    monkeypatch.setattr(rolling_update, "log_action", lambda *a, **k: logged.append(a))
    assert _run(database, [])[0] == "termine"
    assert fake.rebooted == []
    assert ("alice", "host_reboot_required", "pve2", "succes", "left by the rolling update") in logged


def test_this_node_leaves_maintenance_at_its_next_start_when_its_update_restarts_it(cluster, database):
    cluster()
    assert _run(database, [], restarts=True)[0] == "termine"
    assert [m["node"] for m in maintenance.list_all()] == [maintenance.LOCAL]
    rolling_update.finish_self_after_restart()  # the new version's start-up
    assert maintenance.list_all() == []
    rolling_update.finish_self_after_restart()  # once only
    assert maintenance.list_all() == []


def test_an_offline_node_refuses_the_run(cluster, database, monkeypatch):
    fake = cluster()
    monkeypatch.setattr(rolling_update, "peers", lambda: [NODES[0], {**NODES[1], "statut": "hors_ligne"}])
    status, error = _run(database, [])
    assert status == "echec" and "offline" in error and fake.calls == []


def test_the_token_goes_through_ssh_input_never_the_command_line(monkeypatch):
    seen = {}

    def run(argv, input=None, **k):
        seen["argv"], seen["input"] = argv, input
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": 202, "body": '{"task_id": "t1"}'}), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(rolling_update.cluster, "node_ssh_options", lambda extra=None: ["-i", "key", *(extra or [])])
    status, body = rolling_update.peer_api(NODES[0], "alice", "POST", "/update/apply", {"systeme": True})
    assert (status, body) == (202, {"task_id": "t1"})
    request = json.loads(seen["input"])
    assert request["url"] == "https://127.0.0.1:8000/update/apply" and request["body"] == {"systeme": True}
    assert request["token"] and request["token"] not in " ".join(seen["argv"])
    assert seen["argv"][-4:-1] == ["python3", "-I", "-c"] and "root@10.0.0.2" in seen["argv"]


def test_an_unreachable_node_is_said(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 255, "", "No route to host")
    )
    monkeypatch.setattr(rolling_update.cluster, "node_ssh_options", lambda extra=None: [])
    with pytest.raises(rolling_update.PeerUnreachable, match="No route to host"):
        rolling_update.peer_api(NODES[0], "alice", "GET", "/update/check")


def test_the_plan_says_what_prevents_starting(monkeypatch):
    def plan(node, username, local_check=None):
        name = node["name"] if node else "pve1"
        return {"nom": name, "joignable": True, "verifiable": True, "canal": "test" if name == "pve3" else "stable",
                "a_faire": name == "pve2"}  # fmt: skip

    monkeypatch.setattr(rolling_update, "peers", lambda: [dict(n) for n in NODES])
    monkeypatch.setattr(rolling_update, "node_plan", plan)
    out = rolling_update.cluster_plan("alice", None)
    assert [n["nom"] for n in out["noeuds"]] == ["pve2", "pve3", "pve1"] and out["a_faire"] is True
    assert any("same update channel" in p for p in out["problemes"])


def test_the_api_outside_a_cluster(client, auth_headers, monkeypatch):
    from app.core import cluster_lead

    monkeypatch.setattr(cluster_lead, "mode", lambda record=True: "local")
    admin = auth_headers("root")
    assert client.get("/cluster/update/plan", headers=admin).status_code == 409
    assert client.post("/cluster/update", json={}, headers=admin).status_code == 409
    assert client.post("/cluster/update", json={}, headers=auth_headers("eve", role="observateur")).status_code == 403
