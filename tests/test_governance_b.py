"""Exclusions, sign-in rules and scheduled access reviews (docs/design/governance.md, sections 4 to 6)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import access_review, permissions, self_node, signin_rules


@pytest.fixture()
def here(monkeypatch):
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    return "node-a"


def bob():
    return {"username": "bob", "role": "viewer"}


def test_exclusion_takes_away_what_assignments_give(database, here):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "web-1")
    permissions.add_pool_member(pool, "db-1")
    permissions.create_acl("user", "bob", "manager", "pool", str(pool))
    permissions.create_exclusion("user", "bob", "*", "vm", "db-1", "the database", "root")
    assert permissions.has_privilege(bob(), "web-1", "vm.power")
    assert not permissions.has_privilege(bob(), "db-1", "vm.power")
    assert not permissions.has_privilege(bob(), "db-1", "vm.view")  # the global read too
    permissions.create_exclusion("user", "bob", "vm.resize", "pool", str(pool), "", "root")
    assert permissions.has_privilege(bob(), "web-1", "vm.power")
    assert not permissions.has_privilege(bob(), "web-1", "vm.resize")
    assert permissions.has_privilege({"username": "root", "role": "admin"}, "db-1", "vm.power")
    explained = {p["privilege"]: p for p in permissions.explain(bob(), "vm", "web-1")}
    assert explained["vm.resize"]["excluded_by"]["privilege"] == "vm.resize"


def test_signin_rule_conditions(database):
    monday_9 = datetime(2026, 11, 16, 9, 0)
    rule = signin_rules.save(
        {"name": "office", "subject_type": "role", "subject_id": "admin", "networks": ["10.0.0.0/8"],
         "days": ["mon", "tue", "wed", "thu", "fri"], "hours": "08:00-19:00", "mode": "enforce"},
        "root",
    )  # fmt: skip
    admin = {"username": "root", "role": "admin"}
    assert signin_rules.refusal(admin, "10.1.2.3", now=monday_9) is None
    assert "outside the networks" in signin_rules.refusal(admin, "192.168.1.5", now=monday_9)
    assert "hours" in signin_rules.refusal(admin, "10.1.2.3", now=monday_9.replace(hour=22))
    assert "day" in signin_rules.refusal(admin, "10.1.2.3", now=monday_9 + timedelta(days=5))
    assert signin_rules.refusal(bob(), "192.168.1.5", now=monday_9) is None  # not an administrator
    root_of_node = {**admin, "local_root": True}  # hyperlite-admin: never held, the way back in
    assert signin_rules.refusal(root_of_node, "1.2.3.4", now=monday_9) is None
    signin_rules.save({**rule, "mode": "watch"}, "root", rule["id"])
    assert signin_rules.refusal(admin, "192.168.1.5", now=monday_9) is None  # watched, journaled
    with pytest.raises(ValueError):
        signin_rules.save({"name": "x", "subject_type": "all", "networks": ["not-a-net"]}, "root")
    with pytest.raises(ValueError):
        signin_rules.save({"name": "x", "subject_type": "all"}, "root")  # no condition
    assert signin_rules._in_hours("22:00-06:00", monday_9.replace(hour=23))


def test_signin_rule_refuses_requests_and_sign_in(client, auth_headers):
    admin = auth_headers("root", "admin")
    body = {"name": "2fa", "subject_type": "user", "subject_id": "bob", "require_2fa": True, "mode": "enforce"}
    assert client.post("/signin-rules", json=body, headers=admin).status_code == 201
    with pytest.raises(AssertionError):
        auth_headers("bob", "viewer")  # the sign-in is refused: no second factor
    lan = {"name": "lan", "subject_type": "user", "subject_id": "carol", "networks": ["10.0.0.0/8"], "mode": "enforce"}
    carol = auth_headers("carol", "viewer")
    assert client.get("/access/me", headers=carol).status_code == 200
    assert client.post("/signin-rules", json=lan, headers=admin).status_code == 201
    signin_rules._forget()
    refused = client.get("/access/me", headers=carol)
    assert refused.status_code == 403 and "Sign-in rule 'lan'" in refused.json()["detail"]
    assert client.get("/signin-rules", headers=carol).status_code == 403


def test_scheduled_review(database, here, make_user):
    make_user("idle", "viewer")
    kept = permissions.create_acl("user", "idle", "drive", "vm", "a")
    removed = permissions.create_acl("user", "idle", "drive", "vm", "b")
    left = permissions.create_acl("user", "idle", "drive", "vm", "c")
    c = access_review.save_campaign(
        {"name": "quarterly", "every_days": 90, "deadline_days": 14, "reviewers": ["lead"], "remove_undecided": True},
        "root",
    )
    now = datetime.now(UTC)
    access_review.tick(now)
    found = {i["acl_id"]: i for i in access_review.items(c["id"])}
    assert set(found) == {kept, removed, left}
    access_review.decide(c["id"], found[kept]["id"], "keep", "lead")
    access_review.decide(c["id"], found[removed]["id"], "remove", "lead")
    assert {a["id"] for a in permissions.list_acl()} == {kept, left}
    access_review.tick(now + timedelta(days=15))  # past the deadline: the undecided one goes
    assert {a["id"] for a in permissions.list_acl()} == {kept}
    after = next(x for x in access_review.campaigns() if x["id"] == c["id"])
    assert after["round_due_at"] is None and after["next_at"] > now.isoformat()


def test_review_routes(client, auth_headers):
    admin = auth_headers("root", "admin")
    lead = auth_headers("lead", "viewer")
    body = {"name": "q", "every_days": 30, "deadline_days": 7, "reviewers": ["lead"]}
    assert client.post("/access/campaigns", json=body | {"deadline_days": 40}, headers=admin).status_code == 422
    c = client.post("/access/campaigns", json=body, headers=admin).json()
    assert [x["id"] for x in client.get("/access/campaigns", headers=lead).json()["items"]] == [c["id"]]
    assert client.get(f"/access/campaigns/{c['id']}/items", headers=lead).status_code == 200
    assert client.get(f"/access/campaigns/{c['id']}/items", headers=auth_headers("eve", "viewer")).status_code == 403
    assert client.post("/access/exclusions", json={"subject_type": "user", "subject_id": "x", "resource_type": "vm",
                                                   "resource_id": "v"}, headers=lead).status_code == 403  # fmt: skip
