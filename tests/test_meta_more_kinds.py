"""Tags and notes on pools (app/core/object_meta.py, app/routers/meta.py)."""

from app.core import permissions


def test_pool_tags(client, auth_headers):
    pool = permissions.create_pool("web")
    admin = auth_headers("root", "admin")
    viewer = auth_headers("bob", "viewer")
    body = {"notes": "the shop", "tags": ["prod", "Web"]}
    assert client.put(f"/meta/pool/{pool}", json=body, headers=viewer).status_code == 403
    saved = client.put(f"/meta/pool/{pool}", json=body, headers=admin).json()
    assert saved["tags"] == ["prod", "web"]
    assert client.get(f"/meta/pool/{pool}", headers=viewer).json()["notes"] == "the shop"
    assert client.put("/meta/pool/999", json=body, headers=admin).status_code == 404
    listed = client.get("/meta", params={"kind": "pool"}, headers=viewer).json()["items"]
    assert [(m["name"], m["tags"]) for m in listed] == [(str(pool), ["prod", "web"])]
    # A steward of the pool may tag it.
    permissions.create_acl("user", "bob", "steward", "pool", str(pool))
    assert client.put(f"/meta/pool/{pool}", json={"notes": "", "tags": ["x"]}, headers=viewer).status_code == 200
