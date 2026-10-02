"""Shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py): writes to SQLite are copied to the daemon, a copy
that fails never fails the write, and the report shows what differs until a seed makes both equal. The daemon is an
in-memory stand-in here; cfs/tests/python/test_shadow.py runs the same path against the real one."""

import json
import os
import re
import subprocess
import time

import pytest

from app.core import object_meta, vm_boot
from app.core.cfs_client import Child, Entry, NotFound, Status
from app.repositories.cfs import shadow
from app.repositories.sqlite.renames import SqliteRenameStore

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


def meta(fake, path):
    return json.loads(fake.files[path])


def test_shadow_mode_is_off_unless_turned_on(database, monkeypatch):
    monkeypatch.delenv("HYPERLITE_CFS_SHADOW", raising=False)
    fake = FakeCfs()
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    object_meta.put("vm", "web", "Front of the shop", ["prod"])
    assert fake.files == {}
    assert shadow.report()["actif"] is False
    with pytest.raises(shadow.ShadowDisabled):
        shadow.seed()


def test_notes_and_tags_are_copied_as_sqlite_holds_them(cfs):
    object_meta.put("vm", "web", "Front of the shop", ["Prod", "db"])
    object_meta.put("node", "pve-a", "Rack 2", [])
    assert meta(cfs, "/meta/vm/local/web") == {"notes": "Front of the shop", "tags": ["prod", "db"]}
    assert meta(cfs, "/meta/node/_/pve-a") == {"notes": "Rack 2", "tags": []}

    object_meta.put("vm", "web", "", [])  # emptied: SQLite drops the row, the copy goes too
    object_meta.delete("node", "pve-a")
    assert cfs.files == {}
    assert shadow.report()["ecarts"] == 0


def test_a_migration_and_a_rename_move_the_copy(cfs):
    object_meta.put("vm", "web", "", ["prod"])
    object_meta.follow_migration("web", "local", "pve-b")
    assert list(cfs.files) == ["/meta/vm/pve-b/web"]

    SqliteRenameStore().vm_records("web", "shop", "pve-b", "pve-b:", lambda xml, new: xml)
    assert list(cfs.files) == ["/meta/vm/pve-b/shop"]
    object_meta.put("node", "pve-b", "", ["edge"])
    SqliteRenameStore().node_records("pve-b", "pve-c")
    assert sorted(cfs.files) == ["/meta/node/_/pve-c", "/meta/vm/pve-c/shop"]
    assert shadow.report()["ecarts"] == 0


def test_start_at_boot_settings_are_copied_and_follow_the_vm(cfs):
    vm_boot.set_setting("web", True, 2, 30)
    assert json.loads(cfs.files["/boot/local/web"]) == {"autostart": True, "boot_order": 2, "delay_s": 30}
    vm_boot.follow_migration("web", "local", "pve-b")
    assert list(cfs.files) == ["/boot/pve-b/web"]
    SqliteRenameStore().vm_records("web", "shop", "pve-b", "pve-b:", lambda xml, new: xml)
    SqliteRenameStore().node_records("pve-b", "pve-c")
    assert list(cfs.files) == ["/boot/pve-c/shop"]
    vm_boot.delete_setting("shop", "pve-c")
    assert cfs.files == {}
    state = shadow.report()
    assert state["ecarts"] == 0 and set(state["domaines"]) == {"meta", "boot"}


def test_names_the_daemon_refuses_are_encoded_without_colliding():
    assert shadow.component("web-01.prod") == "web-01.prod"
    for odd in ("my vm", "_x", ".hidden", "", "é"):
        encoded = shadow.component(odd)
        assert encoded.startswith("_") and _COMPONENT.match(encoded)
    assert shadow.component("_x") != shadow.component("x")
    assert len({shadow.component(n) for n in ("a b", "_612062", "a_b")}) == 3


def test_a_daemon_that_is_down_never_fails_the_write_and_the_report_shows_the_gap(cfs):
    cfs.down = True
    object_meta.put("vm", "web", "Front", ["prod"])  # SQLite took it; the copy failed
    assert object_meta.get("vm", "web")["tags"] == ["prod"]
    state = shadow.report()
    assert state["joignable"] is False and state["echecs"] == 1
    assert "/meta/vm/local/web" in state["derniere_erreur"]

    cfs.down = False
    cfs.files["/meta/vm/local/gone"] = b"{}"  # left over from an object deleted while the daemon was away
    state = shadow.report()
    assert state["ecarts"] == 2
    assert state["domaines"]["meta"]["exemples"] == {
        "manquants": ["/meta/vm/local/web"],
        "en_trop": ["/meta/vm/local/gone"],
        "differents": [],
    }

    assert shadow.seed() == {"ecrits": 1, "supprimes": 1}
    assert shadow.report()["ecarts"] == 0


def test_errors_reach_the_client_as_fixed_sentences_not_exception_text(cfs, monkeypatch):
    def broken():
        raise ConnectionRefusedError("[Errno 111] internal detail /run/secret")

    monkeypatch.setattr(shadow, "_get_client", broken)
    object_meta.put("vm", "web", "Front", [])
    state = shadow.report()
    assert "internal detail" not in json.dumps(state)
    assert state["erreur"] == "hyperlite-cfs is not running: nothing answers on its socket"
    assert state["derniere_erreur"] == "/meta/vm/local/web: hyperlite-cfs is not running: nothing answers on its socket"


def test_a_copy_that_differs_is_reported(cfs):
    object_meta.put("vm", "web", "Front", ["prod"])
    cfs.files["/meta/vm/local/web"] = b'{"notes":"Front","tags":[]}'
    meta_report = shadow.report()["domaines"]["meta"]
    assert meta_report["differents"] == 1 and meta_report["exemples"]["differents"] == ["/meta/vm/local/web"]


def test_the_report_and_the_seed_are_for_administrators(client, auth_headers, cfs):
    object_meta.put("vm", "web", "Front", ["prod"])
    cfs.files.clear()
    reader = auth_headers("watcher", "observateur")
    assert client.get("/cfs/shadow", headers=reader).status_code == 403
    assert client.post("/cfs/shadow/seed", headers=reader).status_code == 403

    admin = auth_headers("root", "admin")
    body = client.get("/cfs/shadow", headers=admin).json()
    assert body["actif"] is True and body["ecarts"] == 1
    assert client.post("/cfs/shadow/seed", headers=admin).json() == {"ecrits": 1, "supprimes": 0}
    assert client.get("/cfs/shadow", headers=admin).json()["ecarts"] == 0

    cfs.down = True
    assert client.post("/cfs/shadow/seed", headers=admin).status_code == 503


def test_the_seed_is_refused_while_shadow_mode_is_off(client, auth_headers, cfs, monkeypatch):
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "0")
    response = client.post("/cfs/shadow/seed", headers=auth_headers("root", "admin"))
    assert response.status_code == 409 and "HYPERLITE_CFS_SHADOW=1" in response.json()["detail"]


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
        state = shadow.report()
        assert state["joignable"] and state["demon"]["mode"] == "local"
        assert state["domaines"]["meta"]["entrees"] == 2 and state["ecarts"] == 0
    finally:
        _stop(proc)

    object_meta.put("vm", "db", "", ["prod"])  # the daemon is gone: SQLite still takes it
    assert shadow.report()["joignable"] is False and shadow.report()["echecs"] == 1

    proc = _start(BIN, tmp_path)  # same database: what it held survived the restart
    try:
        assert shadow.report()["domaines"]["meta"]["exemples"]["manquants"] == ["/meta/vm/local/db"]
        assert shadow.seed() == {"ecrits": 1, "supprimes": 0}
        assert shadow.report()["ecarts"] == 0
    finally:
        _stop(proc)
