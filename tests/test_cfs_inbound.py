"""Phase D (app/repositories/cfs/inbound.py): in a cluster, changes other nodes made to the replicated tree reach this
node's SQLite, and the safeguards keep a reset or empty daemon from erasing the configuration."""

import json

import pytest

from app.core import object_meta
from app.core.cfs_client import Child, Entry, NotFound, Status
from app.core.database import get_conn
from app.repositories.cfs import inbound, shadow


class Cluster:
    """The daemon as other nodes see it: a tree with a version that every change moves."""

    path = "fake"

    def __init__(self):
        self.files = {}
        self.version = 0
        self.mode = "cluster"

    def put(self, path, data, expected=-1):
        self.files[path] = bytes(data)
        self.version += 1
        return self.version

    def delete(self, path, expected=-1):
        if path not in self.files:
            raise NotFound(1, "no such entry")
        del self.files[path]
        self.version += 1

    def get(self, path):
        if path not in self.files:
            raise NotFound(1, "no such entry")
        return Entry(data=self.files[path], version=1, mtime=0)

    def list(self, path="/"):
        children = {}
        for p in self.files:
            if p.startswith(path + "/"):
                head, _, rest = p[len(path) + 1 :].partition("/")
                children[head] = children.get(head, False) or bool(rest)
        if not children:
            raise NotFound(1, "no such entry")
        return [Child(name=n, is_dir=d, version=1, size=0) for n, d in sorted(children.items())]

    def status(self):
        return Status(version=self.version, checksum="00", quorate=True, mode=self.mode, entries=0, bytes=0)

    def remote(self, path, row):
        """Another node's change."""
        self.put(path, json.dumps(row, sort_keys=True, separators=(",", ":")).encode())


@pytest.fixture()
def cluster(database, monkeypatch):
    fake = Cluster()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    monkeypatch.setattr(inbound, "state", inbound._State())
    shadow.seed()  # this node joined: the tree holds what it holds, and that version is applied
    return fake


def rows(sql, params=()):
    with get_conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def test_another_nodes_changes_reach_this_node(cluster):
    assert inbound.apply() is None  # nothing new since the seed
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    cluster.remote(
        "/db/object_meta/vm/pve-b/web", {"kind": "vm", "node": "pve-b", "name": "web", "notes": "B", "tags": "[]"}
    )
    assert inbound.apply() == 2
    assert rows("SELECT id, name FROM groups") == [{"id": 7, "name": "ops"}]
    assert object_meta.get("vm", "web", "pve-b")["notes"] == "B"
    assert shadow.pending() == 0  # what came from the tree is not sent back
    assert inbound.apply() is None and shadow.report()["ecarts"] == 0

    cluster.delete("/db/groups/7")
    assert inbound.apply() == 1 and rows("SELECT * FROM groups") == []


def test_a_column_left_out_keeps_this_nodes_value(cluster, make_user):
    make_user("ana", "admin")
    shadow.drain()
    with get_conn() as db:
        db.execute("UPDATE users SET last_login_at = 'here' WHERE username = 'ana'")
        db.commit()
    path = next(p for p in cluster.files if p.startswith("/priv/db/users/"))
    user = json.loads(cluster.files[path])
    user["role"] = "observateur"
    cluster.remote(path, user)
    assert inbound.apply() == 1
    assert rows("SELECT role, last_login_at FROM users WHERE username = 'ana'") == [
        {"role": "observateur", "last_login_at": "here"}
    ]


def test_a_rename_and_a_new_row_under_the_old_name_apply_in_the_right_order(cluster):
    cluster.remote("/db/groups/1", {"id": 1, "name": "ops"})
    inbound.apply()
    cluster.remote("/db/groups/2", {"id": 2, "name": "ops"})  # listed before the rename that frees the name
    cluster.remote("/db/groups/1", {"id": 1, "name": "dev"})
    inbound.apply()
    assert rows("SELECT id, name FROM groups ORDER BY id") == [{"id": 1, "name": "dev"}, {"id": 2, "name": "ops"}]


def test_a_row_without_a_left_out_column_gets_a_value(cluster):
    cluster.remote("/db/vm_auto_cleanup/web", {"vm_name": "web", "inactive_days": 30, "created_at": "t"})
    inbound.apply()
    (row,) = rows("SELECT last_active_at FROM vm_auto_cleanup WHERE vm_name = 'web'")
    assert row["last_active_at"]


def test_this_nodes_changes_go_out_before_anything_is_applied(cluster):
    object_meta.put("vm", "web", "mine", [])  # waiting in the outbox
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    assert inbound.apply() is None and rows("SELECT * FROM groups") == []
    shadow.drain()
    assert inbound.apply() >= 1 and rows("SELECT name FROM groups") == [{"name": "ops"}]
    assert object_meta.get("vm", "web")["notes"] == "mine"


def test_a_reset_or_empty_daemon_is_not_applied(cluster):
    object_meta.put("vm", "web", "kept", [])
    shadow.drain()
    inbound.apply()
    cluster.files.clear()
    cluster.version = 0  # the daemon's database was replaced: older than what this node applied
    assert inbound.apply() is None
    assert "older than" in inbound.report()["probleme"]
    assert object_meta.get("vm", "web")["notes"] == "kept"

    cluster.version = 10_000  # newer, but empty
    assert inbound.apply() is None
    assert "no configuration" in inbound.report()["probleme"]
    assert object_meta.get("vm", "web")["notes"] == "kept"

    shadow.seed()  # an administrator copies this node's database: the safeguard is lifted
    assert inbound.report()["probleme"] is None and inbound.apply() is None


def test_in_local_mode_nothing_is_applied(cluster):
    cluster.mode = "local"
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    assert inbound.apply() is None and rows("SELECT * FROM groups") == []


def test_every_column_left_out_can_be_left_out_of_an_insert(database):
    """A row from another node arrives without its volatile columns: each must be nullable, have a default, or be
    filled (tables.py)."""
    with get_conn() as db:
        for name, table in shadow.tables().items():
            for _cid, column, _type, notnull, default, _pk in db.execute(f'PRAGMA table_info("{name}")'):
                volatile = column not in table.columns
                if volatile and notnull and default is None:
                    assert column in table.fill, f"{name}.{column}"


def test_a_change_this_database_refuses_is_reported_and_nothing_is_applied(cluster):
    cluster.remote("/db/groups/1", {"id": 1, "name": "ops"})
    cluster.remote("/db/groups/2", {"id": 2, "name": "ops"})  # two groups with one name: the unique column refuses
    assert inbound.apply() is None
    assert "groups" in inbound.report()["probleme"]
    assert rows("SELECT * FROM groups") == []  # all or nothing
