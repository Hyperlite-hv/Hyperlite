"""Dashboards and saved queries (app/core/dashboards.py): private, shared with everyone or a group, changed by their
owner or an administrator, checked when saved."""

import pytest


@pytest.fixture()
def people(client, auth_headers):
    return {"ann": auth_headers("ann", role="viewer"), "bob": auth_headers("bob", role="viewer"),
            "root": auth_headers("root2")}  # fmt: skip


def test_a_dashboard_is_private_until_shared(client, people):
    tiles = [{"type": "note", "title": "Hello", "size": 2, "config": {"text": "on call: ann"}}]
    r = client.post("/dashboards", json={"name": "Ops", "tiles": tiles}, headers=people["ann"])
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["tiles"][0]["id"] and d["editable"]
    assert client.get(f"/dashboards/{d['id']}", headers=people["bob"]).status_code == 404
    assert client.get("/dashboards", headers=people["bob"]).json()["items"] == []
    client.patch(f"/dashboards/{d['id']}", json={"shared": "everyone"}, headers=people["ann"])
    seen = client.get(f"/dashboards/{d['id']}", headers=people["bob"]).json()
    assert seen["name"] == "Ops" and not seen["editable"]
    assert client.patch(f"/dashboards/{d['id']}", json={"name": "Mine"}, headers=people["bob"]).status_code == 403
    copy = client.post(f"/dashboards/{d['id']}/copy", json={}, headers=people["bob"]).json()
    assert copy["owner"] == "bob" and copy["shared"] == "private" and copy["tiles"] == d["tiles"]
    # An administrator changes a dashboard it reads.
    assert client.patch(f"/dashboards/{d['id']}", json={"name": "Ops 2"}, headers=people["root"]).status_code == 200
    assert client.delete(f"/dashboards/{d['id']}", headers=people["ann"]).status_code == 200


def test_a_dashboard_shared_with_a_group(client, people, database):
    from app.core import permissions

    gid = permissions.create_group("ops")
    gid = gid if isinstance(gid, int) else gid["id"]
    permissions.add_group_member(gid, "bob")
    d = client.post("/dashboards", json={"name": "Team", "shared": f"group:{gid}"}, headers=people["ann"]).json()
    assert client.get(f"/dashboards/{d['id']}", headers=people["bob"]).status_code == 200
    assert client.get(f"/dashboards/{d['id']}", headers=people["root"]).status_code == 404  # not in the group


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "tiles": [{"type": "iframe"}]},
        {"name": "x", "tiles": [{"type": "note", "size": 4}]},
        {"name": "x", "shared": "group:abc"},
        {"name": "x", "tiles": [{"type": "note"}] * 41},
    ],
)
def test_a_dashboard_is_checked(client, people, body):
    assert client.post("/dashboards", json=body, headers=people["ann"]).status_code == 422


def test_saved_queries(client, people):
    body = {"name": "Stopped prod", "query": "kind:vm state:stopped tag:prod", "view": "bars", "group_by": "node"}
    q = client.post("/saved-queries", json=body, headers=people["ann"]).json()
    assert q["group_by"] == "node"
    bad = {**body, "group_by": "colour"}
    assert client.post("/saved-queries", json=bad, headers=people["ann"]).status_code == 422
    assert client.get("/saved-queries", headers=people["bob"]).json()["items"] == []
    r = client.patch(f"/saved-queries/{q['id']}", json={"view": "count", "shared": "everyone"}, headers=people["ann"])
    assert r.json()["group_by"] is None
    assert [x["name"] for x in client.get("/saved-queries", headers=people["bob"]).json()["items"]] == ["Stopped prod"]
    assert client.delete(f"/saved-queries/{q['id']}", headers=people["bob"]).status_code == 403
    assert client.delete(f"/saved-queries/{q['id']}", headers=people["ann"]).status_code == 200
