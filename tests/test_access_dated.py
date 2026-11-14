"""Dated rights, tag restrictions, on-demand rights and delegated tokens (docs/design/access-2.md, 6 to 8)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import access_requests, object_meta, permissions, self_node


@pytest.fixture()
def here(monkeypatch):
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    return "node-a"


def at(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def bob():
    return {"username": "bob", "role": "viewer"}


def test_an_ended_assignment_grants_nothing_and_is_swept(database, here):
    permissions.create_dated_acl("user", "bob", "operator", "vm", "vm1", at(30), None, "on call", "root", "manual")
    permissions.create_dated_acl("user", "bob", "operator", "vm", "vm2", at(-1), None, "", "root", "manual")
    assert permissions.has_privilege(bob(), "vm1", "vm.power")
    assert not permissions.has_privilege(bob(), "vm2", "vm.power")
    access_requests.sweep()
    assert [a["resource_id"] for a in permissions.list_acl()] == ["vm1"]
    assert permissions.list_acl()[0]["reason"] == "on call"


def test_tag_restriction(database, here):
    permissions.create_dated_acl("user", "bob", "builder", "cluster", "*", None, "dev", "", "root", "manual")
    object_meta.put("vm", "web-dev", "", ["dev"])
    object_meta.put("vm", "web-prod", "", ["prod"])
    assert permissions.has_privilege(bob(), "web-dev", "vm.power")
    assert not permissions.has_privilege(bob(), "web-prod", "vm.power")
    assert not permissions.has_area_privilege(bob(), "node.manage")  # no tag on an area


def test_assignment_with_terms_through_the_api(client, auth_headers, make_user, here):
    make_user("bob", "viewer", "correct horse battery")
    admin = auth_headers("root", "admin")
    body = {"subject_type": "user", "subject_id": "bob", "role": "operator", "resource_type": "vm", "resource_id": "v"}
    assert client.post("/acl", json=body | {"expires_at": at(-5)}, headers=admin).status_code == 422
    assert client.post("/acl", json=body | {"tag": "Not A Tag!"}, headers=admin).status_code == 422
    assert (
        client.post("/acl", json=body | {"expires_at": at(60), "reason": "migration"}, headers=admin).status_code == 201
    )
    listed = client.get("/acl", headers=admin).json()["items"]
    assert listed[0]["expires_at"] and listed[0]["reason"] == "migration"


def test_self_service_right(client, auth_headers, make_user, here):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "vm-a")
    headers = auth_headers("bob", "viewer")
    admin = auth_headers("root", "admin")
    eligible = {"subject_type": "user", "subject_id": "bob", "role": "manager", "resource_type": "pool",
                "resource_id": str(pool), "max_minutes": 60, "approval": "auto"}  # fmt: skip
    eligibility = client.post("/access/eligibility", json=eligible, headers=admin).json()["id"]
    assert not permissions.has_privilege(bob(), "vm-a", "vm.resize")
    ask = {"eligibility_id": eligibility, "minutes": 90, "reason": "incident 42"}
    assert client.post("/access/requests", json=ask, headers=headers).status_code == 422  # past its maximum
    granted = client.post("/access/requests", json=ask | {"minutes": 30}, headers=headers).json()
    assert granted["status"] == "granted" and granted["expires_at"]
    assert permissions.has_privilege(bob(), "vm-a", "vm.resize")
    # Given back early: the right goes.
    assert client.post(f"/access/requests/{granted['id']}/cancel", headers=headers).json()["status"] == "ended"
    assert not permissions.has_privilege(bob(), "vm-a", "vm.resize")
    # Not eligible for another one.
    other = {"eligibility_id": 999, "minutes": 10, "reason": "x"}
    assert client.post("/access/requests", json=other, headers=headers).status_code == 404


def test_right_after_approval(client, auth_headers, make_user, here):
    headers = auth_headers("bob", "viewer")
    admin = auth_headers("root", "admin")
    eligible = {"subject_type": "user", "subject_id": "bob", "role": "maintainer", "resource_type": "node",
                "resource_id": "node-a", "max_minutes": 120, "approval": "approval"}  # fmt: skip
    eligibility = client.post("/access/eligibility", json=eligible, headers=admin).json()["id"]
    ask = {"eligibility_id": eligibility, "minutes": 60, "reason": "kernel update"}
    pending = client.post("/access/requests", json=ask, headers=headers).json()
    assert pending["status"] == "pending"
    assert not permissions.has_area_privilege(bob(), "update.apply")
    assert client.post(f"/access/requests/{pending['id']}/approve", headers=headers).status_code == 403  # not itself
    mine = client.get("/access/requests", headers=headers).json()["items"]
    assert mine[0]["may_decide"] is False
    assert client.post(f"/access/requests/{pending['id']}/approve", headers=admin).json()["status"] == "granted"
    assert permissions.has_area_privilege(bob(), "update.apply")
    assert client.post(f"/access/requests/{pending['id']}/refuse", headers=admin).status_code == 409


def test_self_service_is_checked_by_the_daemon_rule(database, here):
    """Without an eligibility, an account cannot give itself a dated right, whatever the API asked."""
    from app.daemon.ops.access import AclCreateTerms, _may_grant_with_terms

    args = AclCreateTerms(
        subject_type="user", subject_id="bob", role="steward", resource_type="cluster", resource_id="*",
        expires_at=at(30), tag=None, reason="x", granted_by="bob", granted_at=at(0), origin="request",
    )  # fmt: skip
    assert _may_grant_with_terms(bob(), args) is not None
    permissions.create_eligibility("user", "bob", "steward", "cluster", "*", 30, "auto", "root")
    assert _may_grant_with_terms(bob(), args) is None
    longer = args.model_copy(update={"expires_at": at(300)})
    assert _may_grant_with_terms(bob(), longer) is not None


def test_delegated_token(client, auth_headers, make_user, here):
    pool = permissions.create_pool("ci")
    other = permissions.create_pool("prod")
    permissions.add_pool_member(pool, "ci-1")
    permissions.add_pool_member(other, "prod-1")
    permissions.create_acl("user", "bob", "steward", "cluster", "*")
    session = auth_headers("bob", "viewer")
    scope = {"role": "operator", "resource_type": "pool", "resource_id": str(pool)}
    made = client.post("/auth/tokens", json={"name": "ci", "scope": scope}, headers=session).json()
    token = {"Authorization": f"Bearer {made['token']}"}
    me = client.get("/access/me", headers=token).json()
    assert me["role"] == "delegated" and me["delegated"] is True
    from app.core.api_tokens import verify_token

    user = verify_token(made["token"])
    assert permissions.has_privilege(user, "ci-1", "vm.power")
    assert not permissions.has_privilege(user, "ci-1", "vm.resize")  # not an operator's
    assert not permissions.has_privilege(user, "prod-1", "vm.power")  # outside its pool
    body = {
        "subject_type": "user",
        "subject_id": "x",
        "role": "drive",
        "resource_type": "pool",
        "resource_id": str(pool),
    }
    assert client.post("/acl", json=body, headers=token).status_code == 403  # the owner may, the token not
    listed = client.get("/auth/tokens", headers=session).json()["items"]
    assert listed[0]["scope"]["role"] == "operator"
    # The owner loses its rights: the token loses them too.
    permissions.delete_acl(permissions.list_acl()[0]["id"])
    assert not permissions.has_privilege(verify_token(made["token"]), "ci-1", "vm.power")
