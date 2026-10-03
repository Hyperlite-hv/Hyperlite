"""This node's own name in the tables every node of a cluster shares (app/core/self_node.py): the rows name it, the
application and the API keep calling it "local"."""

import json

import pytest

from tests.test_nodes_contract import _add, nodes_api  # noqa: F401  (fixture)


def _rows(database, sql):
    with database.get_conn() as db:
        return [tuple(r) for r in db.execute(sql).fetchall()]


def test_rows_store_the_node_name_and_read_back_as_local(database):
    from app.core import maintenance, object_meta, vm_boot

    object_meta.put("vm", "web", "Front", ["prod"], None)
    object_meta.put("node", "local", "This one", [], None)
    vm_boot.set_setting("web", True, 1, 0, None)
    maintenance.enter("local", "alice")

    assert _rows(database, "SELECT kind, node, name FROM object_meta ORDER BY kind") == [
        ("node", "", "hv-test"),
        ("vm", "hv-test", "web"),
    ]
    assert _rows(database, "SELECT node, vm_name FROM vm_boot") == [("hv-test", "web")]
    assert _rows(database, "SELECT node FROM node_maintenance") == [("hv-test",)]

    assert object_meta.get("vm", "web", None)["notes"] == "Front"
    assert object_meta.get("node", "local", None)["notes"] == "This one"
    assert {(m["kind"], m["node"], m["nom"]) for m in object_meta.list_all()} == {
        ("vm", "local", "web"),
        ("node", None, "local"),
    }
    assert vm_boot.get_setting("web", None)["demarrage_auto"]
    assert maintenance.get("local") is not None


def test_a_migration_moves_rows_to_the_target_node_and_back(database):
    from app.core import object_meta, vm_boot

    object_meta.put("vm", "web", "Front", [], None)
    vm_boot.set_setting("web", True, None, 0, None)
    object_meta.follow_migration("web", "local", "pve-b")
    vm_boot.follow_migration("web", "local", "pve-b")
    assert _rows(database, "SELECT node FROM object_meta") == [("pve-b",)]
    assert _rows(database, "SELECT node FROM vm_boot") == [("pve-b",)]
    object_meta.follow_migration("web", "pve-b", "local")
    assert object_meta.get("vm", "web", None)["notes"] == "Front"


def test_local_rows_written_before_the_upgrade_are_renamed_at_start(database):
    from app.core import maintenance, object_meta, self_node

    with database.get_conn() as db:
        db.execute("INSERT INTO vm_boot (node, vm_name, autostart) VALUES ('local', 'web', 1)")
        db.execute("INSERT INTO object_meta (kind, node, name, notes) VALUES ('vm', 'local', 'web', 'Old')")
        db.execute("INSERT INTO object_meta (kind, node, name, notes) VALUES ('node', '', 'local', 'Mine')")
        db.execute("INSERT INTO node_maintenance (node, started_by, started_at) VALUES ('local', 'bob', 't')")
        db.execute(
            "INSERT INTO shared_pools (nom, definition, noeuds) VALUES ('nfs', '{}', ?)",
            (json.dumps(["local", "pve-b"]),),
        )
        db.execute("DELETE FROM cfs_outbox")
        db.commit()

    self_node.forget()
    database.init_db()  # the next start

    assert _rows(database, "SELECT node FROM vm_boot") == [("hv-test",)]
    assert _rows(database, "SELECT kind, node, name FROM object_meta ORDER BY kind") == [
        ("node", "", "hv-test"),
        ("vm", "hv-test", "web"),
    ]
    assert _rows(database, "SELECT node FROM node_maintenance") == [("hv-test",)]
    assert json.loads(_rows(database, "SELECT noeuds FROM shared_pools")[0][0]) == ["hv-test", "pve-b"]
    assert object_meta.get("vm", "web", None)["notes"] == "Old"
    assert maintenance.get("local")["started_by"] == "bob"
    # The renamed rows go to hyperlite-cfs like any other change.
    assert ("vm_boot", '["hv-test","web"]') in _rows(database, "SELECT tbl, pk FROM cfs_outbox")


def test_the_name_comes_from_the_host_once_then_stays(database, monkeypatch):
    from app.core import self_node

    monkeypatch.delenv("HYPERLITE_NODE_NAME")
    with database.get_conn() as db:  # a database that never recorded a name
        db.execute("DELETE FROM app_settings WHERE cle = 'node_name'")
        db.commit()
    monkeypatch.setattr(self_node.socket, "gethostname", lambda: "PVE_A.example.org")
    self_node.forget()
    assert self_node.name() == "pve-a"
    assert _rows(database, "SELECT valeur FROM app_settings WHERE cle = 'node_name'") == [("pve-a",)]

    monkeypatch.setattr(self_node.socket, "gethostname", lambda: "renamed")
    self_node.forget()
    assert self_node.name() == "pve-a"  # renaming the host changes nothing
    self_node.forget()
    database.init_db()
    assert self_node.name() == "pve-a"


@pytest.mark.parametrize("host", ["local", "x", "--"])
def test_a_host_name_that_cannot_be_a_node_name_gets_a_prefix(monkeypatch, host):
    from app.core import self_node

    monkeypatch.setattr(self_node.socket, "gethostname", lambda: host)
    name = self_node._from_hostname()
    assert name.startswith("node-") and self_node.NAME_RE.match(name)


def test_an_invalid_forced_name_is_refused(database, monkeypatch):
    from app.core import self_node

    for bad in ("local", "a", "no spaces"):
        monkeypatch.setenv("HYPERLITE_NODE_NAME", bad)
        with pytest.raises(ValueError):
            self_node.name()


def test_this_node_is_never_listed_among_the_remote_nodes(nodes_api, database):  # noqa: F811
    client, admin, viewer = nodes_api
    _add(client, admin, "pve-b", "10.0.0.2")
    with database.get_conn() as db:  # the shared table lists every member, this one included
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('hv-test', 'self.lan', 't')")
        db.commit()
    assert [n["name"] for n in client.get("/nodes", headers=viewer).json()] == ["pve-b"]
    from app.repositories.sqlite.nodes import SqliteNodeStore

    assert SqliteNodeStore().get("hv-test") is None and SqliteNodeStore().get("pve-b") is not None


def test_a_node_cannot_be_registered_or_renamed_under_this_node_name(nodes_api):  # noqa: F811
    client, admin, _viewer = nodes_api
    for name in ("hv-test", "HV-TEST", "local"):
        r = _add(client, admin, name)
        assert r.status_code == 422 and "own name" in r.json()["detail"]
    assert _add(client, admin, "pve-b").status_code == 201
    r = client.post("/nodes/pve-b/rename", json={"new_name": "hv-test"}, headers=admin)
    assert r.status_code == 422


def test_a_task_names_its_node_and_this_node_finds_it_as_local(database):
    """The node pages ask for node=local: tasks recorded the host name libvirt gave, so they found none."""
    from app.core.tasks import create_task
    from app.repositories.sqlite.tasks import SqliteTaskStore

    create_task("start_vm", "web")  # local
    create_task("stop_vm", "web", node="local")
    create_task("start_vm", "db", node="pve-b")  # a registered node keeps its name

    assert _rows(database, "SELECT node FROM tasks ORDER BY cible, type") == [("pve-b",), ("hv-test",), ("hv-test",)]
    store = SqliteTaskStore()
    assert sorted(t["cible"] for t in store.list({"node": "local"})) == ["web", "web"]
    assert [t["cible"] for t in store.list({"node": "pve-b"})] == ["db"]


def test_tasks_recorded_under_a_host_name_become_this_nodes_once(database):
    from app.core import self_node

    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('pve-b', '192.0.2.2', 't')")
        db.execute("DELETE FROM app_settings WHERE cle = ?", (self_node.TASKS_SETTING,))
        for i, node in enumerate(("hyperlite.home", None, "local", "pve-b", "hv-test")):
            db.execute(
                "INSERT INTO tasks (id, type, cible, node, statut, progres, cree_le) VALUES (?, 'start_vm', ?, ?, 'termine', 100, 't')",
                (f"t{i}", f"vm{i}", node),
            )
        db.commit()

    self_node.forget()
    database.init_db()  # the next start

    assert _rows(database, "SELECT cible, node FROM tasks ORDER BY cible") == [
        ("vm0", "hv-test"),
        ("vm1", "hv-test"),
        ("vm2", "hv-test"),
        ("vm3", "pve-b"),
        ("vm4", "hv-test"),
    ]
    # Once only: a later task of a node that left the cluster keeps its name.
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO tasks (id, type, cible, node, statut, progres, cree_le) VALUES ('t9', 'start_vm', 'vm9', 'gone', 'termine', 100, 't')"
        )
        db.commit()
    self_node.forget()
    database.init_db()
    assert _rows(database, "SELECT node FROM tasks WHERE id = 't9'") == [("gone",)]
