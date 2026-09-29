"""Notes and tags on VMs, containers and nodes."""

import libvirt
import pytest

from app.core import object_meta


class _Conn:
    def __init__(self, names):
        self.names = set(names)

    def lookupByName(self, name):
        if name not in self.names:
            raise libvirt.libvirtError("no domain")
        return object()

    def close(self):
        pass


@pytest.fixture()
def hosts(monkeypatch):
    from app.routers import meta

    monkeypatch.setattr(meta, "open_conn", lambda node=None: _Conn({"web", "db"} if not node else {"far"}))
    monkeypatch.setattr(meta, "open_lxc_conn", lambda: _Conn({"cache"}))


def test_tags_are_short_lowercase_words_without_duplicates():
    assert object_meta.normalize_tags([" Prod ", "db", "prod", "", "client-x.eu"]) == ["prod", "db", "client-x.eu"]
    for bad in (["two words"], ["<script>"], ["-leading"], ["x" * 33]):
        with pytest.raises(object_meta.MetaError):
            object_meta.normalize_tags(bad)
    with pytest.raises(object_meta.MetaError):
        object_meta.normalize_tags([f"t{i}" for i in range(object_meta.MAX_TAGS + 1)])


def test_notes_and_tags_are_stored_per_object_and_cleared_when_empty(database):
    object_meta.put("vm", "web", "Front of the shop.\r\nAsk Ana.", ["prod"])
    object_meta.put("vm", "web", "Same name on another node", ["lab"], node="n2")
    object_meta.put("node", "local", "", ["rack-a"])
    assert object_meta.get("vm", "web") == {"notes": "Front of the shop.\nAsk Ana.", "tags": ["prod"]}
    assert object_meta.get("vm", "web", "n2")["tags"] == ["lab"]
    listing = object_meta.list_all("vm")
    assert {(m["node"], m["nom"], tuple(m["tags"]), m["a_des_notes"]) for m in listing} == {
        ("local", "web", ("prod",), True),
        ("n2", "web", ("lab",), True),
    }
    assert "notes" not in listing[0]  # the listing stays light: notes are read one object at a time
    assert object_meta.list_all("node")[0]["node"] is None
    object_meta.put("vm", "web", "   ", [])
    assert object_meta.get("vm", "web") == {"notes": "", "tags": []}
    assert len(object_meta.list_all("vm")) == 1


def test_notes_and_tags_follow_a_migration_and_go_with_the_vm(database):
    object_meta.put("vm", "db", "primary", ["prod"])
    assert object_meta.follow_migration("db", None, "n2")
    assert object_meta.get("vm", "db", "n2")["notes"] == "primary"
    object_meta.delete("vm", "db", "n2")
    assert object_meta.list_all() == []


def test_the_api_checks_rights_and_existence(client, auth_headers, hosts):
    from app.core import permissions

    admin = auth_headers("admin")
    r = client.put("/meta/vm/web", json={"notes": "Shop front", "tags": ["Prod", "web"]}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"notes": "Shop front", "tags": ["prod", "web"]}
    assert client.get("/meta/vm/web", headers=admin).json()["tags"] == ["prod", "web"]
    assert client.put("/meta/vm/far", json={"tags": ["x"]}, headers=admin).status_code == 404  # not on this host
    assert client.put("/meta/vm/far?node=n2", json={"tags": ["x"]}, headers=admin).status_code == 200
    assert client.put("/meta/vm/ghost", json={"tags": ["x"]}, headers=admin).status_code == 404
    assert client.put("/meta/container/cache", json={"notes": "redis"}, headers=admin).status_code == 200
    assert client.put("/meta/node/local", json={"tags": ["rack-a"]}, headers=admin).status_code == 200
    assert client.put("/meta/node/nowhere", json={"tags": ["x"]}, headers=admin).status_code == 404
    assert client.put("/meta/pool/x", json={}, headers=admin).status_code == 404
    assert client.put("/meta/vm/web", json={"tags": ["not a tag"]}, headers=admin).status_code == 422
    assert (
        client.put("/meta/vm/web", json={"notes": "x" * (object_meta.MAX_NOTES + 1)}, headers=admin).status_code == 422
    )

    viewer = auth_headers("viewer", role="observateur")
    assert client.get("/meta/vm/web", headers=viewer).json()["notes"] == "Shop front"
    assert client.put("/meta/vm/web", json={"notes": "mine now"}, headers=viewer).status_code == 403
    assert client.put("/meta/node/local", json={"notes": "x"}, headers=viewer).status_code == 403
    assert {m["nom"] for m in client.get("/meta?kind=vm", headers=viewer).json()} == {"web", "far"}

    permissions.create_acl("user", "otto", "operateur", "vm", "web")
    permissions.create_acl("user", "mia", "gestionnaire", "vm", "web")
    otto, mia = auth_headers("otto", role="observateur"), auth_headers("mia", role="observateur")
    assert client.get("/meta/vm/web", headers=otto).status_code == 200
    assert client.put("/meta/vm/web", json={"notes": "x"}, headers=otto).status_code == 403
    assert client.put("/meta/vm/web", json={"notes": "by mia", "tags": ["prod"]}, headers=mia).status_code == 200
    assert client.put("/meta/vm/db", json={"notes": "x"}, headers=mia).status_code == 403  # no ACL on that VM
