"""Shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py): SQLite triggers record every change of a
configuration table, drain() copies them to the daemon, a copy that fails never fails the write and is retried, and the
report shows what differs. The daemon is an in-memory stand-in here, except in test_against_the_real_daemon."""

import json
import os
import re
import subprocess
import time

import pytest

from app.core import object_meta, vm_boot
from app.core.cfs_client import Child, Entry, NotFound, Status
from app.core.database import get_conn
from app.repositories.cfs import shadow
from app.repositories.sqlite.renames import SqliteRenameStore
from app.repositories.sqlite.settings import SqliteSettingsStore

_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class FakeCfs:
    """The daemon's tree semantics that shadow mode relies on: files at paths, directories implied by them."""

    path = "fake"

    def __init__(self):
        self.files = {}
        self.down = False

    def _up(self):
        if self.down:
            raise ConnectionRefusedError("hyperlite-cfs is not running")

    def put(self, path, data, expected=-1):
        self._up()
        assert all(_COMPONENT.match(c) and c not in (".", "..") for c in path.split("/")[1:]), path
        self.files[path] = bytes(data)
        return len(self.files)

    def delete(self, path, expected=-1):
        self._up()
        if path not in self.files:
            raise NotFound(1, "no such entry")
        del self.files[path]

    def get(self, path):
        self._up()
        if path not in self.files:
            raise NotFound(1, "no such entry")
        return Entry(data=self.files[path], version=1, mtime=0)

    def list(self, path="/"):
        self._up()
        children = {}
        for p in self.files:
            if p.startswith(path + "/"):
                head, _, rest = p[len(path) + 1 :].partition("/")
                children[head] = children.get(head, False) or bool(rest)
        if not children:
            raise NotFound(1, "no such entry")
        return [Child(name=n, is_dir=d, version=1, size=0) for n, d in sorted(children.items())]

    def status(self):
        self._up()
        return Status(
            version=len(self.files), checksum="00", quorate=True, mode="local", entries=len(self.files), bytes=0
        )


@pytest.fixture()
def cfs(database, monkeypatch):
    fake = FakeCfs()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    return fake


def row(fake, path):
    return json.loads(fake.files[path])


def sql(statement, params=()):
    with get_conn() as db:
        db.execute(statement, params)
        db.commit()


def test_shadow_mode_is_off_unless_turned_on(database, monkeypatch):
    monkeypatch.delenv("HYPERLITE_CFS_SHADOW", raising=False)
    fake = FakeCfs()
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    object_meta.put("vm", "web", "Front of the shop", ["prod"])
    assert shadow.drain() == 0 and fake.files == {}
    assert shadow.pending() == 0  # nothing is kept for a daemon that is not used
    assert shadow.report()["actif"] is False
    with pytest.raises(shadow.ShadowDisabled):
        shadow.seed()


def test_notes_and_tags_are_copied_as_sqlite_holds_them(cfs):
    object_meta.put("vm", "web", "Front of the shop", ["Prod", "db"])
    object_meta.put("node", "pve-a", "Rack 2", [])
    assert shadow.pending() == 2 and shadow.drain() == 2 and shadow.pending() == 0
    web = row(cfs, "/db/object_meta/vm/local/web")
    assert web["notes"] == "Front of the shop" and json.loads(web["tags"]) == ["prod", "db"]
    assert row(cfs, "/db/object_meta/node/_/pve-a")["notes"] == "Rack 2"  # a node's own notes: node is ''

    object_meta.put("vm", "web", "", [])  # emptied: SQLite drops the row, the copy goes too
    object_meta.delete("node", "pve-a")
    shadow.drain()
    assert cfs.files == {}
    assert shadow.report()["ecarts"] == 0


def test_a_migration_and_renames_move_the_copy(cfs):
    object_meta.put("vm", "web", "", ["prod"])
    vm_boot.set_setting("web", True, 2, 30)
    object_meta.follow_migration("web", "local", "pve-b")
    vm_boot.follow_migration("web", "local", "pve-b")
    shadow.drain()
    assert sorted(cfs.files) == ["/db/object_meta/vm/pve-b/web", "/db/vm_boot/pve-b/web"]
    assert row(cfs, "/db/vm_boot/pve-b/web") == {
        "node": "pve-b",
        "vm_name": "web",
        "autostart": 1,
        "boot_order": 2,
        "delay_s": 30,
    }

    # Renames move rows with raw SQL (app/repositories/sqlite/renames.py): the triggers see those writes too.
    SqliteRenameStore().vm_records("web", "shop", "pve-b", "pve-b:", lambda xml, new: xml)
    object_meta.put("node", "pve-b", "", ["edge"])
    SqliteRenameStore().node_records("pve-b", "pve-c")
    shadow.drain()
    assert sorted(cfs.files) == [
        "/db/object_meta/node/_/pve-c",
        "/db/object_meta/vm/pve-c/shop",
        "/db/vm_boot/pve-c/shop",
    ]
    vm_boot.delete_setting("shop", "pve-c")
    shadow.drain()
    assert "/db/vm_boot/pve-c/shop" not in cfs.files
    assert shadow.report()["ecarts"] == 0


def test_every_write_is_seen_even_raw_sql_and_secrets_go_under_priv(cfs, make_user):
    sql("INSERT INTO groups (name) VALUES ('ops')")
    make_user("ana", "admin")
    shadow.drain()
    assert row(cfs, "/db/groups/1") == {"id": 1, "name": "ops"}
    users = [p for p in cfs.files if p.startswith("/priv/db/users/")]
    assert len(users) == 1 and not any(p.startswith("/db/users") for p in cfs.files)
    user = row(cfs, users[0])
    assert user["username"] == "ana" and "last_login_at" not in user

    # A sign-in only changes a column that says nothing about the configuration: nothing to copy.
    sql("UPDATE users SET last_login_at = '2026-10-02T12:00:00' WHERE username = 'ana'")
    assert shadow.pending() == 0


def test_the_mirror_switch_itself_stays_on_each_node(cfs):
    SqliteSettingsStore().set_app_setting("cfs_shadow", "1")
    SqliteSettingsStore().set_app_setting("api_docs_acces", "admins")
    shadow.drain()
    assert "/db/app_settings/api_docs_acces" in cfs.files
    assert "/db/app_settings/cfs_shadow" not in cfs.files
    assert shadow.report()["ecarts"] == 0


def test_tables_of_state_and_history_are_not_copied(cfs):
    sql("INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES ('t', 'ana', 'x', 'y', 'succes')")
    sql("INSERT INTO login_failures (kind, key, at) VALUES ('user', 'ana', 1)")
    assert shadow.pending() == 0
    assert "audit_log" not in shadow.tables() and "users" in shadow.tables()


def test_names_the_daemon_refuses_are_encoded_without_colliding():
    assert shadow.component("web-01.prod") == "web-01.prod"
    for odd in ("my vm", "_x", ".hidden", "", "é"):
        encoded = shadow.component(odd)
        assert encoded.startswith("_") and _COMPONENT.match(encoded)
    assert shadow.component("_x") != shadow.component("x")
    assert len({shadow.component(n) for n in ("a b", "_612062", "a_b")}) == 3


def test_a_daemon_that_is_down_never_fails_the_write_and_is_caught_up_on(cfs):
    cfs.down = True
    object_meta.put("vm", "web", "Front", ["prod"])  # SQLite took it; the copy waits
    assert object_meta.get("vm", "web")["tags"] == ["prod"]
    assert shadow.drain() == 0 and shadow.pending() == 1
    state = shadow.report()
    assert state["joignable"] is False and state["echecs"] == 1 and state["en_attente"] == 1
    assert "/db/object_meta/vm/local/web" in state["derniere_erreur"]

    cfs.down = False
    assert shadow.drain() == 1 and shadow.pending() == 0  # the daemon answers again: the copy goes through
    cfs.files["/db/object_meta/vm/local/gone"] = b"{}"  # left over from elsewhere
    state = shadow.report()
    assert state["ecarts"] == 1 and state["domaines"]["object_meta"]["exemples"]["en_trop"] == [
        "/db/object_meta/vm/local/gone"
    ]
    assert shadow.seed()["supprimes"] == 1
    assert shadow.report()["ecarts"] == 0


def test_errors_reach_the_client_as_fixed_sentences_not_exception_text(cfs, monkeypatch):
    def broken():
        raise ConnectionRefusedError("[Errno 111] internal detail /run/secret")

    monkeypatch.setattr(shadow, "_get_client", broken)
    object_meta.put("vm", "web", "Front", [])
    assert shadow.drain() == 0 and shadow.pending() == 1
    state = shadow.report()
    assert "internal detail" not in json.dumps(state)
    assert state["erreur"] == "hyperlite-cfs is not running: nothing answers on its socket"
    assert state["derniere_erreur"] == "hyperlite-cfs: hyperlite-cfs is not running: nothing answers on its socket"


def test_a_copy_that_differs_is_reported(cfs):
    object_meta.put("vm", "web", "Front", ["prod"])
    shadow.drain()
    cfs.files["/db/object_meta/vm/local/web"] = b'{"notes":"Front","tags":"[]"}'
    report = shadow.report()["domaines"]["object_meta"]
    assert report["differents"] == 1 and report["exemples"]["differents"] == ["/db/object_meta/vm/local/web"]


def test_the_report_and_the_seed_are_for_administrators(client, auth_headers, cfs):
    object_meta.put("vm", "web", "Front", ["prod"])
    reader = auth_headers("watcher", "observateur")
    assert client.get("/cfs/shadow", headers=reader).status_code == 403
    assert client.post("/cfs/shadow/seed", headers=reader).status_code == 403

    admin = auth_headers("root", "admin")
    body = client.get("/cfs/shadow", headers=admin).json()
    assert body["actif"] is True and body["ecarts"] >= 3 and body["en_attente"] >= 3  # the note and two accounts
    seeded = client.post("/cfs/shadow/seed", headers=admin).json()
    assert seeded["ecrits"] >= 3 and seeded["supprimes"] == 0
    assert client.get("/cfs/shadow", headers=admin).json()["ecarts"] == 0

    cfs.down = True
    assert client.post("/cfs/shadow/seed", headers=admin).status_code == 503


def test_the_seed_is_refused_while_shadow_mode_is_off(client, auth_headers, cfs, monkeypatch):
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "0")
    response = client.post("/cfs/shadow/seed", headers=auth_headers("root", "admin"))
    assert response.status_code == 409 and "turn it on" in response.json()["detail"]


BIN = os.environ.get("HYPERLITE_CFS_BIN", "")


def _start(bin_path, tmp_path):
    sock = tmp_path / "cfs.sock"
    proc = subprocess.Popen([bin_path, "--db", str(tmp_path / "cfs.db"), "--socket", str(sock)], stderr=subprocess.PIPE)
    for _ in range(200):
        if sock.exists():
            return proc
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode())
        time.sleep(0.02)
    proc.kill()
    raise RuntimeError("hyperlite-cfs did not create its socket")


def _stop(proc):
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.skipif(not BIN, reason="HYPERLITE_CFS_BIN is not set (the CI's backend job builds the daemon)")
def test_against_the_real_daemon(database, tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setenv("HYPERLITE_CFS_SOCKET", str(tmp_path / "cfs.sock"))
    monkeypatch.setattr(shadow, "_client", None)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    proc = _start(BIN, tmp_path)
    try:
        object_meta.put("vm", "my web", "Front", ["prod"])  # a name the daemon only takes encoded
        object_meta.put("node", "pve-a", "Rack 2", [])
        assert shadow.drain() == 2
        state = shadow.report()
        assert state["joignable"] and state["demon"]["mode"] == "local"
        assert state["domaines"]["object_meta"]["entrees"] == 2 and state["ecarts"] == 0
        # /priv is served to root only: run as another user (the CI), those tables are marked, not failed.
        assert state["domaines"]["users"].get("illisible", False) is (os.geteuid() != 0)
    finally:
        _stop(proc)

    object_meta.put("vm", "db", "", ["prod"])  # the daemon is gone: SQLite still takes it, the copy waits
    assert shadow.drain() == 0 and shadow.pending() == 1
    assert shadow.report()["joignable"] is False and shadow.report()["echecs"] == 1

    proc = _start(BIN, tmp_path)  # same database: what it held survived the restart
    try:
        assert shadow.report()["domaines"]["object_meta"]["exemples"]["manquants"] == ["/db/object_meta/vm/local/db"]
        assert shadow.drain() == 1  # caught up on without a seed
        assert shadow.report()["ecarts"] == 0
    finally:
        _stop(proc)


@pytest.fixture()
def switch(database, tmp_path, monkeypatch):
    """Shadow mode off, a daemon installed and systemctl recorded instead of run."""
    fake = FakeCfs()
    monkeypatch.delenv("HYPERLITE_CFS_SHADOW", raising=False)
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    binary = tmp_path / "hyperlite-cfs"
    binary.write_text("")
    monkeypatch.setattr(shadow, "BINARY", str(binary))
    calls = []
    fake.systemctl_ok = True

    def systemctl(*args):
        calls.append(args)
        return fake.systemctl_ok

    monkeypatch.setattr(shadow, "_systemctl", systemctl)
    fake.calls = calls
    fake.binary = binary
    return fake


def test_the_button_starts_the_daemon_turns_shadow_mode_on_and_copies(client, auth_headers, switch):
    object_meta.put("vm", "web", "Front", ["prod"])  # written while shadow mode is off: not copied
    assert switch.files == {}
    admin = auth_headers("root", "admin")
    assert client.post("/cfs/shadow/activer", headers=auth_headers("watcher", "observateur")).status_code == 403

    assert client.post("/cfs/shadow/activer", headers=admin).json()["supprimes"] == 0
    assert switch.calls == [("enable", "--now")]
    assert shadow.enabled() and "/db/object_meta/vm/local/web" in switch.files
    object_meta.put("vm", "db", "", ["prod"])  # from now on every change is copied
    shadow.drain()
    assert "/db/object_meta/vm/local/db" in switch.files
    body = client.get("/cfs/shadow", headers=admin).json()
    assert body["actif"] and not body["force"] and body["installe"] and body["ecarts"] == 0

    assert client.post("/cfs/shadow/desactiver", headers=admin).json() == {"actif": False}
    assert switch.calls[-1] == ("disable", "--now")
    assert not shadow.enabled()
    object_meta.put("vm", "cache", "", ["prod"])
    shadow.drain()
    assert "/db/object_meta/vm/local/cache" not in switch.files


def test_the_button_says_why_it_cannot_turn_shadow_mode_on(client, auth_headers, switch):
    admin = auth_headers("root", "admin")
    switch.systemctl_ok = False
    response = client.post("/cfs/shadow/activer", headers=admin)
    assert response.status_code == 503 and "did not start" in response.json()["detail"]
    assert not shadow.enabled()

    switch.binary.unlink()
    response = client.post("/cfs/shadow/activer", headers=admin)
    assert response.status_code == 503 and "not installed" in response.json()["detail"]
    assert client.get("/cfs/shadow", headers=admin).json()["installe"] is False


def test_shadow_mode_forced_in_env_cannot_be_turned_off_from_the_page(client, auth_headers, switch, monkeypatch):
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    response = client.post("/cfs/shadow/desactiver", headers=auth_headers("root", "admin"))
    assert response.status_code == 409 and "HYPERLITE_CFS_SHADOW=1" in response.json()["detail"]
    assert switch.calls == []


def test_every_mirrored_table_has_a_label_in_both_languages():
    from pathlib import Path

    from app.repositories.cfs.tables import TABLES

    root = Path(__file__).resolve().parent.parent / "dashboard" / "src" / "next" / "i18n"
    for lang in ("en", "fr"):
        text = (root / f"{lang}.js").read_text()
        missing = [t for t in TABLES if f'"cfs.domainName.{t}":' not in text]
        assert not missing, f"{lang}.js has no label for {missing}"
