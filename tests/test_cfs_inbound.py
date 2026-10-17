"""Phase D (app/repositories/cfs/inbound.py): in a cluster, changes other nodes made to the replicated tree reach this
node's SQLite, and the safeguards keep a reset or empty daemon from erasing the configuration."""

import json

import pytest

from app.core import cluster_lead, object_meta
from app.core.cfs_client import Child, Conflict, Entry, NotFound, Status
from app.core.database import get_conn
from app.repositories.cfs import ids, inbound, shadow


class Cluster:
    """The daemon as other nodes see it: a tree with a version that every change moves, and the version of each entry
    for compare-and-set (cfs/src/store.c, check_expected)."""

    path = "fake"

    def __init__(self):
        self.files = {}
        self.versions = {}
        self.version = 0
        self.mode = "cluster"
        self.counter = 100  # the daemon's id counter (app/repositories/cfs/ids.py)

    def put(self, path, data, expected=-1):
        if expected == 0 and path in self.files:
            raise Conflict(4, "the entry exists")
        if expected > 0 and path not in self.files:
            raise NotFound(1, "no such entry")
        if expected > 0 and self.versions.get(path, 0) != expected:
            raise Conflict(4, "the entry changed")
        self.version += 1
        self.files[path] = bytes(data)
        self.versions[path] = self.version
        return self.version

    def delete(self, path, expected=-1):
        if path not in self.files:
            raise NotFound(1, "no such entry")
        if expected != -1 and self.versions.get(path, 0) != expected:
            raise Conflict(4, "the entry changed")
        del self.files[path]
        self.versions.pop(path, None)
        self.version += 1

    def get(self, path):
        if path not in self.files:
            raise NotFound(1, "no such entry")
        # Other tests write files directly: such an entry has version 0 until it is put.
        return Entry(data=self.files[path], version=self.versions.get(path, 0), mtime=0)

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

    def next_id(self):
        self.counter += 1
        self.version += 1
        return self.counter - 1

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
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    cluster_lead.forget()
    ids.forget()
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


def test_a_sign_out_on_one_node_revokes_the_session_on_every_node(cluster):
    from app.core import security

    # Signed out here: the revocation goes out, so another node refuses the token too.
    token = security.create_access_token({"sub": "ana"})
    assert security.revoke_session(token)
    shadow.drain()
    jti = security._decode_session(token)["jti"]
    assert any(p.startswith("/db/revoked_sessions/") for p in cluster.files)

    # Signed out on another node: this node refuses the token once the tree is applied.
    other = security._decode_session(security.create_access_token({"sub": "ana"}))
    assert not security._session_revoked(other["jti"])
    cluster.remote(f"/db/revoked_sessions/{other['jti']}", {"jti": other["jti"], "expires_at": other["exp"]})
    assert inbound.apply() == 1
    assert security._session_revoked(other["jti"]) and security._session_revoked(jti)


# ---- The majority wins: a change made here never overwrites a newer one of the cluster ----


def write(sql, params=()):
    with get_conn() as db:
        db.execute(sql, params)
        db.commit()


def test_a_change_made_here_never_overwrites_a_newer_one_of_the_cluster(cluster):
    from app.core import audit

    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    assert inbound.apply() == 1  # this node holds the group at the cluster's version
    cluster.remote("/db/groups/7", {"id": 7, "name": "by-the-majority"})  # renamed by the others
    write("UPDATE groups SET name = 'cut-off' WHERE id = 7")  # renamed here meanwhile (this node was cut off)
    assert shadow.drain() == 0 and shadow.pending() == 0  # dropped, not copied
    assert json.loads(cluster.files["/db/groups/7"])["name"] == "by-the-majority"
    assert inbound.apply() == 1  # the cluster's version comes back here, though the tree did not move since
    assert rows("SELECT name FROM groups WHERE id = 7") == [{"name": "by-the-majority"}]
    assert shadow.report()["conflits"] == 1
    audit._AUDIT_QUEUE.join()
    assert rows("SELECT resource FROM audit_log WHERE action = 'cfs_conflict'") == [{"resource": "/db/groups/7"}]


def test_a_change_of_an_entry_nobody_else_changed_goes_out(cluster):
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    inbound.apply()
    write("UPDATE groups SET name = 'ops2' WHERE id = 7")
    assert shadow.drain() == 1 and json.loads(cluster.files["/db/groups/7"])["name"] == "ops2"
    write("UPDATE groups SET name = 'ops3' WHERE id = 7")  # its own write is the version the next one starts from
    assert shadow.drain() == 1 and json.loads(cluster.files["/db/groups/7"])["name"] == "ops3"
    assert shadow.report()["conflits"] == 0


def test_an_entry_another_node_created_first_is_not_overwritten(cluster):
    path = "/db/object_meta/vm/hv-test/web"
    cluster.remote(path, {"kind": "vm", "node": "hv-test", "name": "web", "notes": "theirs", "tags": "[]"})
    object_meta.put("vm", "web", "mine", [])  # created here before this node saw theirs
    assert shadow.drain() == 0
    assert json.loads(cluster.files[path])["notes"] == "theirs"
    inbound.apply()
    assert object_meta.get("vm", "web")["notes"] == "theirs"


def test_a_row_the_cluster_deleted_is_not_brought_back(cluster):
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    cluster.remote("/db/groups/8", {"id": 8, "name": "dev"})  # the tree is never empty in a cluster (a safeguard)
    inbound.apply()
    cluster.delete("/db/groups/7")  # deleted by the others
    write("UPDATE groups SET name = 'renamed-here' WHERE id = 7")
    assert shadow.drain() == 0 and "/db/groups/7" not in cluster.files
    assert inbound.apply() == 1, inbound.state.problem
    assert rows("SELECT id FROM groups") == [{"id": 8}]


def test_a_new_row_of_this_node_goes_out(cluster):
    object_meta.put("vm", "db", "notes", [])
    assert shadow.drain() == 1 and "/db/object_meta/vm/hv-test/db" in cluster.files


def test_a_single_node_writes_as_before(cluster):
    """Outside a cluster (the daemon in local mode) this node is the only writer: nothing is compared, nothing dropped,
    even an entry whose version this node never recorded (right after an update)."""
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    inbound.apply()
    cluster.mode = "local"
    write("DELETE FROM cfs_base")
    cluster.remote("/db/groups/7", {"id": 7, "name": "stale"})
    write("UPDATE groups SET name = 'mine' WHERE id = 7")
    assert shadow.drain() == 1 and json.loads(cluster.files["/db/groups/7"])["name"] == "mine"
    assert shadow.report()["conflits"] == 0


def test_both_sides_wrote_the_same_value_is_no_conflict(cluster):
    cluster.remote("/db/groups/7", {"id": 7, "name": "ops"})
    inbound.apply()
    cluster.remote("/db/groups/7", {"id": 7, "name": "same"})  # another node wrote it...
    write("UPDATE groups SET name = 'same' WHERE id = 7")  # ...to the value this node wrote too
    assert shadow.drain() == 1 and shadow.report()["conflits"] == 0
    write("UPDATE groups SET name = 'next' WHERE id = 7")  # and its next change starts from that version
    assert shadow.drain() == 1 and json.loads(cluster.files["/db/groups/7"])["name"] == "next"
