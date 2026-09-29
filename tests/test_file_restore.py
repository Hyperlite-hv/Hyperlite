"""File-level restore: sessions over a helper process (a stand-in speaking the helper's protocol) and the API."""

import json
import sys
import textwrap

import pytest

from app.core import file_restore as fr
from app.core.database import get_conn

FAKE_HELPER = textwrap.dedent(
    """
    import json, sys
    disks = sys.argv[1:]
    if any(d.endswith("broken.qcow2") for d in disks):
        print("cannot open the disk image", file=sys.stderr, flush=True)
        sys.exit(1)
    tree = {"/": ["etc", "notes.txt"], "/etc": ["app.conf"]}
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if req["op"] == "filesystems":
            res = {"filesystems": [{"device": "/dev/sda1", "type": "ext4", "size": 1024, "label": "root"}]}
        elif req["op"] == "ls":
            if req["device"] != "/dev/sda1":
                res = {"error": "No filesystem " + req["device"] + " in this backup"}
            elif req["path"] not in tree:
                res = {"error": req["path"] + " is not a directory"}
            else:
                res = {"entries": [{"name": n, "type": "dir" if "/" + n in tree or req["path"] + "/" + n in tree else "file", "size": 5, "mtime": 0} for n in tree[req["path"]]]}
        elif req["op"] == "export":
            with open(req["dest"], "w") as f:
                f.write("content of " + req["path"])
            res = {"kind": "dir" if req["path"] in tree else "file"}
        elif req["op"] == "close":
            break
        print(json.dumps(res), flush=True)
    """
)


@pytest.fixture()
def restore(database, tmp_path, monkeypatch):
    helper = tmp_path / "helper.py"
    helper.write_text(FAKE_HELPER)
    monkeypatch.setattr(fr, "HELPER", helper)
    monkeypatch.setattr(fr, "_interpreter", [sys.executable])
    monkeypatch.setattr(fr, "_sessions", {})
    backup = tmp_path / "bk"
    backup.mkdir()
    (backup / "sda.qcow2").write_text("x")
    (backup / "manifest.json").write_text(json.dumps({"fichiers": [{"nom": "sda.qcow2", "role": "disque"}]}))
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backups (id, vm_name, chemin, statut, cree_le, mode) VALUES (1, 'web', ?, 'termine', '2026-01-01', 'froid')",
            (str(backup),),
        )
        conn.execute(
            "INSERT INTO backups (id, vm_name, chemin, statut, cree_le, mode) VALUES (2, 'web', ?, 'echec', '2026-01-01', 'froid')",
            (str(backup),),
        )
        conn.commit()
    yield backup
    for s in list(fr._sessions.values()):
        s.close()


def test_browse_and_download_through_the_api(client, auth_headers, restore):
    admin = auth_headers("root", "admin")
    r = client.post("/backups/1/files", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vm"] == "web" and body["filesystems"][0]["device"] == "/dev/sda1"
    sid = body["session"]
    assert client.post("/backups/1/files", headers=admin).json()["session"] == sid  # the open session is reused

    ls = client.get(f"/file-restore/{sid}/ls", params={"device": "/dev/sda1", "path": "/"}, headers=admin).json()
    assert [(e["name"], e["type"]) for e in ls["entries"]] == [("etc", "dir"), ("notes.txt", "file")]
    r = client.get(f"/file-restore/{sid}/ls", params={"device": "/dev/sda1", "path": "/nope"}, headers=admin)
    assert r.status_code == 422 and "not a directory" in r.json()["detail"]

    r = client.get(f"/file-restore/{sid}/download", params={"device": "/dev/sda1", "path": "/notes.txt"}, headers=admin)
    assert r.status_code == 200 and r.text == "content of /notes.txt"
    assert 'filename="notes.txt"' in r.headers["content-disposition"]
    r = client.get(f"/file-restore/{sid}/download", params={"device": "/dev/sda1", "path": "/etc"}, headers=admin)
    assert 'filename="etc.tar.gz"' in r.headers["content-disposition"]
    assert list(fr._sessions[sid].workdir.glob("export-*")) == []  # temporary copies removed once sent

    assert (
        client.get(
            f"/file-restore/{sid}/ls", params={"device": "/dev/sda1"}, headers=auth_headers("watcher", "observateur")
        ).status_code
        == 403
    )
    assert client.delete(f"/file-restore/{sid}", headers=admin).status_code == 200
    assert client.get(f"/file-restore/{sid}/ls", params={"device": "/dev/sda1"}, headers=admin).status_code == 422


def test_refusals(client, auth_headers, restore, monkeypatch):
    admin = auth_headers("root", "admin")
    assert "finished backup" in client.post("/backups/2/files", headers=admin).json()["detail"]
    assert "not found" in client.post("/backups/9/files", headers=admin).json()["detail"]
    (restore / "manifest.json").write_text(json.dumps({"fichiers": [{"nom": "broken.qcow2", "role": "disque"}]}))
    (restore / "broken.qcow2").write_text("x")
    r = client.post("/backups/1/files", headers=admin)
    assert r.status_code == 422 and "cannot open the disk image" in r.json()["detail"]
    monkeypatch.setattr(fr, "_interpreter", [None])
    assert "python3-guestfs" in client.post("/backups/1/files", headers=admin).json()["detail"]
    assert client.get("/file-restore/status", headers=admin).json()["disponible"] is False


def test_sessions_are_per_user_and_limited(restore, monkeypatch, tmp_path):
    s = fr.open_session(1, "alice")
    with pytest.raises(fr.RestoreError, match="does not exist"):
        fr.get_session(s.id, "bob")
    monkeypatch.setattr(fr, "MAX_SESSIONS", 1)
    with pytest.raises(fr.RestoreError, match="being browsed already"):
        fr.open_session(1, "bob")
