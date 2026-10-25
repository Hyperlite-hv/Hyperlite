"""Load balancing applied (docs/design/drs.md 3.5, 3.7, #334 step 4) and its API: the moves asked for by the leader
and carried out by the member each VM runs on, two at a time and one per member, a pause after two failures, an
administrator's move sent to that member, and the "must" rules on an administrator's start and migration."""

import importlib

import pytest
from fastapi import HTTPException

from app.core import cluster_lead, drs, drs_rules, self_node, tasks

migration = importlib.import_module("app.routers.vms.migration")
lifecycle = importlib.import_module("app.routers.vms.lifecycle")


@pytest.fixture()
def world(database, monkeypatch):
    monkeypatch.setattr(drs, "_moved", {})
    monkeypatch.setattr(drs, "_running", {})
    monkeypatch.setattr(drs, "_failures", [0])
    monkeypatch.setattr(
        drs,
        "_state",
        {"unbalanced": 0, "at": None, "spread": None, "moves": [], "nodes": {}, "reason": None, "paused": None},
    )
    me = {"name": "n1"}
    monkeypatch.setattr(self_node, "name", lambda: me["name"])
    monkeypatch.setattr(self_node, "is_self", lambda n: n == me["name"])
    started, statuses, notes = [], {}, []

    def start(name, source, target, username, skip_checks=False):
        started.append((name, source, target, username))
        task_id = f"t{len(started)}"
        statuses[task_id] = "running"
        return {"task_id": task_id, "status": "running"}

    monkeypatch.setattr(migration, "start", start)
    monkeypatch.setattr(tasks, "task_status", lambda task_id: statuses.get(task_id))
    monkeypatch.setattr(drs, "_notify", lambda title, message, result="failed": notes.append(title))
    return {"me": me, "started": started, "statuses": statuses, "notes": notes}


def move(vm, source, target):
    return {"vm": vm, "source": source, "target": target, "gain": 10.0, "reason": "spread"}


def requests():
    return [(r["vm_name"], r["source"], r["target"], r["state"]) for r in drs_rules._store().requests()]


def on(world, node):
    world["me"]["name"] = node
    drs._carry_out()


def test_automatic_mode_asks_two_at_a_time_one_per_member_never_a_manual_vm(world):
    moves = [
        move("a", "n1", "n2"),
        move("b", "n1", "n3"),
        move("c", "n3", "n4"),
        move("d", "n5", "n6"),
        move("e", "n7", "n8"),
    ]
    drs._automatic(moves, {"d": "manual"})
    # a (n1 -> n2); b shares n1 with a; c (n3 -> n4); then the cluster's two are asked for.
    assert requests() == [("a", "n1", "n2", "pending"), ("c", "n3", "n4", "pending")]
    drs._automatic(moves, {"d": "manual"})
    assert len(requests()) == 2  # still running: nothing more
    assert world["started"] == []  # the leader starts none itself


def test_each_member_carries_out_the_moves_of_its_own_vms_and_says_how_they_ended(world):
    drs._automatic([move("a", "n1", "n2"), move("c", "n3", "n4")], {})
    on(world, "n3")  # the member c runs on, in load balancing's name
    assert world["started"] == [("c", None, "n4", "drs")] and world["notes"] == ["Load balancing: c moved to n4"]
    on(world, "n1")
    assert [s[0] for s in world["started"]] == ["c", "a"]
    assert {r[0]: r[3] for r in requests()} == {"a": "started", "c": "started"}
    world["statuses"].update(t1="failed", t2="succeeded")  # c's task, then a's
    on(world, "n3")
    on(world, "n1")
    assert {r[0]: r[3] for r in requests()} == {"a": "succeeded", "c": "failed"}
    world["me"]["name"] = "n9"  # the leader, back to its round
    drs._automatic([], {})
    assert drs.state()["running"] == [] and drs._failures == [1]


def test_two_failures_in_a_row_pause_automatic_mode_until_the_settings_are_saved(world):
    drs._automatic([move("a", "n1", "n2"), move("b", "n3", "n4")], {})
    for r in drs_rules._store().requests():
        drs_rules._store().update_request(r["id"], "failed", "now", detail="refused")
    drs._automatic([move("c", "n5", "n6")], {})
    assert drs.state()["paused"] and world["notes"][-1] == "Load balancing paused"
    assert [r[0] for r in requests()] == ["a", "b"]  # paused: c not asked for
    drs.resume()
    drs._automatic([move("c", "n5", "n6")], {})
    assert requests()[-1][0] == "c"


def test_a_move_the_migration_refuses_fails_and_is_not_asked_again_within_the_hour(world, monkeypatch):
    def refuse(*a, **k):
        raise HTTPException(status_code=409, detail="refused by the compatibility diagnostic")

    monkeypatch.setattr(migration, "start", refuse)
    drs._automatic([move("a", "n1", "n2")], {})
    on(world, "n1")
    row = drs_rules._store().requests()[0]
    assert row["state"] == "failed" and "compatibility" in row["detail"]
    assert "a" in drs._moved and not drs._movable("a", {"shared": True}, {}, now=drs._moved["a"] + 60)


def test_a_move_no_member_picks_up_fails_after_a_while(world, monkeypatch):
    drs._automatic([move("a", "n5", "n2")], {})
    monkeypatch.setattr(drs.time, "monotonic", lambda: 10**9)
    drs._automatic([], {})
    row = drs_rules._store().requests()[0]
    assert row["state"] == "failed" and "did not pick it up" in row["detail"] and drs._failures == [1]


def test_an_administrators_move_goes_to_the_member_the_vm_runs_on(world, monkeypatch):
    from app.core import cluster_view, rolling_update

    calls = []
    monkeypatch.setattr(cluster_view, "peer", lambda n: {"name": n})

    def peer_api(member, username, method, path, body=None, **k):
        calls.append((member["name"], username, method, path, body))
        return (
            (202, {"task_id": "remote", "status": "running"})
            if body["target_node"] != "n4"
            else (409, {"detail": "no"})
        )

    monkeypatch.setattr(rolling_update, "peer_api", peer_api)
    assert drs.apply(move("web", "n1", "n2"), "root")["task_id"] == "t1"  # on this node
    assert drs.apply(move("db", "n3", "n2"), "root")["task_id"] == "remote"
    assert calls == [("n3", "root", "POST", "/vms/db/migrate", {"target_node": "n2"})]
    with pytest.raises(HTTPException) as err:
        drs.apply(move("db", "n3", "n4"), "root")
    assert err.value.status_code == 409 and err.value.detail == "no"


def test_a_start_or_a_migration_that_breaks_a_must_rule_is_refused(database, monkeypatch):
    drs_rules.save_rule({"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}, "admin")
    monkeypatch.setattr(drs_rules, "placement", lambda: {"dns1": "n2"})
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    with pytest.raises(HTTPException) as err:
        lifecycle._keep_drs_rules("dns2", "n2", "admin")
    assert err.value.status_code == 409 and "dns" in err.value.detail
    lifecycle._keep_drs_rules("dns2", None, "admin")  # on n1: fine
    with pytest.raises(HTTPException) as err:
        migration._keep_drs_rules("dns2", "n2")
    assert err.value.status_code == 409


def test_the_api(client, auth_headers, world, monkeypatch):
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: True)
    admin, viewer = auth_headers("root", "admin"), auth_headers("eve", "viewer")
    body = client.get("/drs", headers=viewer).json()
    assert body["settings"] == {"mode": "automatic", "threshold": 3} and body["state"]["leading"] is True
    assert client.put("/drs/settings", json={"mode": "manual", "threshold": 4}, headers=viewer).status_code == 403
    assert client.put("/drs/settings", json={"mode": "manual", "threshold": 4}, headers=admin).json() == {
        "mode": "manual",
        "threshold": 4,
    }
    assert client.put("/drs/settings", json={"mode": "never", "threshold": 3}, headers=admin).status_code == 422
    rule = {"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}
    assert client.put("/drs/rules/dns", json=rule, headers=admin).json()["kind"] == "apart"
    assert client.put("/drs/rules/other", json=rule, headers=admin).status_code == 422  # the name of the path
    assert client.put("/drs/rules/x", json={**rule, "name": "x", "vms": ["one"]}, headers=admin).status_code == 422
    assert [r["name"] for r in client.get("/drs/rules", headers=viewer).json()["items"]] == ["dns"]
    assert client.put("/drs/vms/db", json={"behaviour": "excluded"}, headers=admin).status_code == 200
    assert client.get("/drs", headers=viewer).json()["behaviours"] == {"db": "excluded"}
    r = client.post("/drs/apply", json={"vm": "web", "source": "n1", "target": "n2"}, headers=admin)
    assert r.status_code == 202 and world["started"][-1] == ("web", None, "n2", "root")
    assert client.delete("/drs/rules/dns", headers=admin).status_code == 200
    assert client.delete("/drs/rules/dns", headers=admin).status_code == 404
