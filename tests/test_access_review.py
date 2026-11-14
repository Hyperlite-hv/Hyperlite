"""The access review (docs/design/access-2.md, section 8)."""

from datetime import UTC, datetime, timedelta

from app.core import permissions


def test_review_flags_and_keep(client, auth_headers, make_user):
    make_user("old", "viewer", "correct horse battery")  # never signed in
    admin = auth_headers("root", "admin")
    pool = permissions.create_pool("web")
    kept = permissions.create_acl("user", "old", "access_delegate", "pool", str(pool))
    permissions.create_acl("user", "gone", "drive", "vm", "vm1")
    permissions.create_acl("group", "999", "drive", "vm", "vm1")
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    permissions.create_dated_acl("user", "root", "builder", "cluster", "*", soon, None, "", "root", "manual")

    answer = client.get("/access/review", headers=admin).json()
    flags = {(a["subject_id"], a["resource_type"]): set(a["flags"]) for a in answer["assignments"]}
    assert {"stale_account", "gives_access", "to_review"} <= flags[("old", "pool")]
    assert "orphan" in flags[("gone", "vm")] and "orphan" in flags[("999", "vm")]
    assert {"whole_cluster", "ending_soon"} <= flags[("root", "cluster")]
    assert "old" in {u["username"] for u in answer["stale_accounts"]}
    assert "root" in answer["admins_without_2fa"]
    assert answer["summary"]["orphans"] == 2

    assert client.post(f"/access/review/{kept}/keep", json={"note": "team lead"}, headers=admin).status_code == 200
    row = next(a for a in client.get("/access/review", headers=admin).json()["assignments"] if a["id"] == kept)
    assert "to_review" not in row["flags"] and row["review"]["note"] == "team lead"
    assert client.post("/access/review/9999/keep", json={}, headers=admin).status_code == 404


def test_review_is_for_cluster_readers(client, auth_headers):
    viewer = auth_headers("bob", "viewer")
    assert client.get("/access/review", headers=viewer).status_code == 403
    permissions.create_acl("user", "bob", "observer", "cluster", "*")
    assert client.get("/access/review", headers=viewer).status_code == 200
    assert client.post("/access/review/1/keep", json={}, headers=viewer).status_code == 403  # keeping is a decision
