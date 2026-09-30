"""Contract of the node endpoints, written against the behaviour before the Node domain moved behind a repository
(docs/design/control-plane-v2-migration.md, lot 1): the status codes, the JSON the dashboard reads and the rows left
in the database must not change. SSH and libvirt are replaced by fakes."""

import pytest

NODE_KEYS = {"id", "name", "hostname", "ssh_user", "ssh_port", "statut", "derniere_verification", "added_at", "live"}


@pytest.fixture()
def nodes_api(client, auth_headers, monkeypatch):
    from app.core import cluster
    from app.routers import nodes

    monkeypatch.setattr(cluster, "test_node_connection", lambda host, user, port: (True, host))
    monkeypatch.setattr(cluster, "ensure_reverse_trust", lambda node: None)
    monkeypatch.setattr(cluster, "forget_known_host", lambda node: None)
    monkeypatch.setattr(cluster, "revoke_reverse_trust", lambda name: None)
    monkeypatch.setattr(cluster, "rename_reverse_trust", lambda old, new: None)
    monkeypatch.setattr(nodes, "rename_reverse_trust", lambda old, new: None)
    monkeypatch.setattr(nodes, "_compat_with_local", lambda name: {"compatible": True})
    monkeypatch.setattr("app.routers.storage.apply_shared_pools", lambda name, username: [])
    monkeypatch.setattr(nodes, "node_summary", lambda name: {"hostname": f"{name}.lan", "connecte": True})
    return client, auth_headers("alice"), auth_headers("olga", role="observateur")


def _add(client, headers, name="n2", host="10.0.0.2"):
    return client.post("/nodes", json={"name": name, "hostname": host}, headers=headers)


def test_registering_a_node_returns_it_with_its_checks(nodes_api):
    client, admin, _viewer = nodes_api
    r = _add(client, admin)
    assert r.status_code == 201
    body = r.json()
    assert {k: body[k] for k in ("name", "hostname", "ssh_user", "ssh_port", "statut")} == {
        "name": "n2",
        "hostname": "10.0.0.2",
        "ssh_user": "root",
        "ssh_port": 22,
        "statut": "en_ligne",
    }
    assert body["pools_partages"] == [] and body["compatibilite"] == {"compatible": True}
    assert _add(client, admin).status_code == 422  # the same name twice
    assert _add(client, admin, name="bad name").status_code == 422


def test_the_list_carries_every_field_and_the_live_figures(nodes_api, database):
    client, admin, viewer = nodes_api
    _add(client, admin, "n3", "10.0.0.3")
    _add(client, admin, "n2", "10.0.0.2")
    with database.get_conn() as db:
        db.execute("INSERT INTO node_live (name, ts, joignable, cpu_pct) VALUES ('n2', 't', 1, 12.5)")
        db.commit()
    listed = client.get("/nodes", headers=viewer).json()
    assert [n["name"] for n in listed] == ["n2", "n3"]  # sorted by name
    assert set(listed[0]) == NODE_KEYS
    assert listed[0]["live"]["cpu_pct"] == 12.5 and listed[1]["live"] is None


def test_a_node_summary_and_its_404(nodes_api):
    client, admin, viewer = nodes_api
    _add(client, admin)
    summary = client.get("/nodes/n2/summary", headers=viewer)
    assert summary.status_code == 200 and summary.json()["hostname"] == "n2.lan" and "live" in summary.json()
    assert client.get("/nodes/nope/summary", headers=viewer).status_code == 404
    assert client.get("/nodes/nope/compatibility", headers=admin).status_code == 404
    assert client.get("/nodes/nope/capabilities", headers=viewer).status_code == 404


def test_renaming_and_removing_a_node(nodes_api, database):
    client, admin, viewer = nodes_api
    _add(client, admin, "n2", "10.0.0.2")
    _add(client, admin, "n3", "10.0.0.3")
    assert client.post("/nodes/n2/rename", json={"new_name": "x"}, headers=viewer).status_code == 403
    assert client.post("/nodes/n2/rename", json={"new_name": "n3"}, headers=admin).status_code == 409
    assert client.post("/nodes/nope/rename", json={"new_name": "n9"}, headers=admin).status_code == 404
    r = client.post("/nodes/n2/rename", json={"new_name": "rack2"}, headers=admin)
    assert r.status_code == 200 and set(r.json()) == NODE_KEYS and r.json()["name"] == "rack2"

    assert client.delete("/nodes/rack2", headers=viewer).status_code == 403
    assert client.delete("/nodes/rack2", headers=admin).json() == {"message": "Node 'rack2' removed"}
    assert client.delete("/nodes/rack2", headers=admin).status_code == 404
    with database.get_conn() as db:
        assert [r[0] for r in db.execute("SELECT name FROM nodes")] == ["n3"]


def test_the_ha_list_reports_each_node_state(nodes_api, database):
    client, admin, viewer = nodes_api
    _add(client, admin)
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, enabled_by, enabled_at) VALUES "
            "('web', 'n2', 'a', 'now'), ('db', 'local', 'a', 'now'), ('old', 'gone', 'a', 'now')"
        )
        db.commit()
    states = {r["vm_name"]: r["statut_noeud"] for r in client.get("/ha", headers=viewer).json()}
    assert states == {"web": "en_ligne", "db": "en_ligne", "old": "inconnu"}
