"""Load balancing applied (docs/design/drs.md 3.5, 3.7, #334 step 4) and its API: the moves through the live
migration, two at a time and one per member, a pause after two failures, and the "must" rules on an
administrator's start and migration."""

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
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    started, statuses, notes = [], {}, []

    def start(name, source, target, username, skip_checks=False):
        started.append((name, source, target, username))
        task_id = f"t{len(started)}"
        statuses[task_id] = "running"
        return {"task_id": task_id, "status": "running"}

    monkeypatch.setattr(migration, "start", start)
    monkeypatch.setattr(tasks, "task_status", lambda task_id: statuses.get(task_id))
    monkeypatch.setattr(drs, "_notify", lambda title, message, result="failed": notes.append(title))
    return {"started": started, "statuses": statuses, "notes": notes}


def move(vm, source, target):
    return {"vm": vm, "source": source, "target": target, "gain": 10.0, "reason": "spread"}


def test_automatic_mode_moves_two_at_a_time_one_per_member_never_a_manual_vm(world):
    moves = [
        move("a", "n1", "n2"),
        move("b", "n1", "n3"),
        move("c", "n3", "n4"),
        move("d", "n5", "n6"),
        move("e", "n7", "n8"),
    ]
    drs._automatic(moves, {"d": "manual"})
    # a (n1 -> n2); b shares n1 with a; c (n3 -> n4); then the cluster's two are running.
    assert world["started"] == [("a", None, "n2", "drs"), ("c", "n3", "n4", "drs")]
    assert world["notes"] == ["Load balancing: a moved to n2", "Load balancing: c moved to n4"]
    drs._automatic(moves, {"d": "manual"})
    assert len(world["started"]) == 2  # still running: nothing more


def test_two_failures_in_a_row_pause_automatic_mode_until_the_settings_are_saved(world):
    drs._automatic([move("a", "n1", "n2"), move("b", "n3", "n4")], {})
    world["statuses"].update(t1="failed", t2="failed")
    drs._automatic([move("c", "n5", "n6")], {})
    assert drs.state()["paused"] and world["notes"][-1] == "Load balancing paused"
    assert [s[0] for s in world["started"]] == ["a", "b"]  # paused: c not moved
    drs.resume()
    drs._automatic([move("c", "n5", "n6")], {})
    assert world["started"][-1][0] == "c"


def test_a_move_the_migration_refuses_is_not_tried_again_within_the_hour(world, monkeypatch):
    def refuse(*a, **k):
        raise HTTPException(status_code=409, detail="refused by the compatibility diagnostic")

    monkeypatch.setattr(migration, "start", refuse)
    drs._automatic([move("a", "n1", "n2")], {})
    assert "a" in drs._moved and not drs._movable("a", {"shared": True}, {}, now=drs._moved["a"] + 60)


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
