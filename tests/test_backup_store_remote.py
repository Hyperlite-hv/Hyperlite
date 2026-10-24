"""Copies of another cluster's backup store (docs/design/backup-store.md, step 4, part 2): the source serves a store
read-only to an account that holds backup_store.read on it and nothing else; the copy's side reaches it over pinned
HTTPS with that account's token, makes a copy store with the source's chunk names and no key, pulls its backups, and
is given the key only on the day it restores. Both sides are this test's application: the remote's HTTPS calls go
through the test client."""

import urllib.parse
import uuid

import pytest

from app.core import backup_remotes, backup_store, backup_store_sync, backup_stores, permissions
from app.core.api_tokens import create_token

VM = str(uuid.uuid4())
DATA = (b"remote disk " * 200000)[:1_500_000]


@pytest.fixture()
def pools(tmp_path, monkeypatch):
    found = {"local": tmp_path / "local", "remote": tmp_path / "remote"}
    for path in found.values():
        path.mkdir()
    monkeypatch.setattr(backup_stores, "_pool_dir", lambda name: found[name])
    return found


@pytest.fixture()
def source(database, pools, make_user):
    """An encrypted store at the source with one backup, and a dedicated reader account's token."""
    _, code = backup_stores.create("main", "local", encrypted=True)
    store = backup_stores.open_store("main")
    index = store.write_disk(lambda o, n: DATA[o : o + n], len(DATA))
    store.write_backup(VM, "2026-10-24T02:00:00Z", {"vda": index}, {"vm": "web", "mode": "live"}, {"nvram.fd": b"NV"})
    make_user("copier", "viewer", "correct horse battery")
    permissions.create_acl("user", "copier", "store_reader", "backup_store", "main")
    _, token = create_token("copier", "offsite")
    return {"code": code, "token": token}


@pytest.fixture()
def wire(client, monkeypatch):
    """The remote's API reached through the test client, as backup_remotes._Client would reach it over HTTPS."""
    calls = []

    def get(self, path):
        calls.append(path)
        r = client.get(path.removeprefix(backup_remotes.API), headers={"Authorization": f"Bearer {self.token}"})
        return r.status_code, r.content

    monkeypatch.setattr(backup_remotes._Client, "get", get)
    monkeypatch.setattr(backup_remotes._Client, "close", lambda self: None)
    return calls


def _remote(source):
    return backup_remotes.save("site-a", "10.0.0.5", 8000, "ab" * 32, source["token"], "ana")


def test_the_reader_account_reads_the_store_and_nothing_else(client, source, wire):
    headers = {"Authorization": f"Bearer {source['token']}"}
    export = client.get("/backup-stores/main/export", headers=headers).json()
    assert export["config"]["encrypted"] and "key" not in str(export["config"]).replace("key_fingerprint", "")
    [backup] = export["backups"]
    stamp = urllib.parse.quote(backup["time"])
    raw = client.get(f"/backup-stores/main/export/backups/{VM}/{stamp}/nvram.fd", headers=headers)
    assert raw.status_code == 200 and raw.content != b"NV"  # sealed: the reader holds no key
    assert client.get(f"/backup-stores/main/export/backups/{VM}/{stamp}/..%2Fx", headers=headers).status_code == 404
    assert client.get("/backup-stores/main/export/chunks/" + "0" * 64, headers=headers).status_code == 404
    # Nothing else: not another store, not the VMs, not a deletion.
    backup_stores.create("other", "local", "other")
    assert client.get("/backup-stores/other/export", headers=headers).status_code == 403
    assert client.delete("/backup-stores/main", headers=headers).status_code == 403
    assert client.post("/backup-store-syncs/1/run", headers=headers).status_code == 403


def test_another_cluster_pulls_a_copy_without_the_key_and_unlocks_it_to_restore(client, source, wire):
    _remote(source)
    assert [s["name"] for s in backup_remotes.stores("site-a")] == ["main"]
    row, shown = backup_stores.create("offsite", "remote", copy_of="site-a:main")
    assert shown is None and row["copy_of"] == "site-a:main"
    assert backup_stores.describe(row)["locked"]  # encrypted, and no key here
    job = backup_store_sync.save_job(
        {"name": "from-a", "source": "site-a:main", "target": "offsite", "frequency": "daily", "time": "05:00"}
    )
    result = backup_store_sync.run_job(job)
    assert result["copied"] == 1 and result["chunks"] >= 1
    assert backup_store_sync.run_job(job)["copied"] == 0  # nothing new
    copy = backup_stores.open_store("offsite", raw=True)
    [(vm, stamp, manifest)] = copy.backups()
    assert copy.verify_raw(vm, stamp) == [] and manifest["copied_from"]
    with pytest.raises(backup_stores.StoreError, match="keeps no key on this site"):
        backup_stores.open_store("offsite")
    with pytest.raises(backup_stores.StoreError, match="not the store's key"):
        backup_stores.unlock("offsite", backup_store.key_code(backup_store.new_key()))
    unlocked = backup_stores.describe(backup_stores.unlock("offsite", source["code"]))
    assert not unlocked["locked"]
    store = backup_stores.open_store("offsite")
    out = bytearray(len(DATA))
    store.read_disk(store.index_of(vm, stamp, "vda"), lambda o, c: out.__setitem__(slice(o, o + len(c)), c))
    assert bytes(out) == DATA and store.read_file(vm, stamp, "nvram.fd") == b"NV"


def test_a_remote_is_saved_only_with_a_token_it_accepts(source, wire):
    with pytest.raises(backup_remotes.RemoteError, match="refused the token"):
        backup_remotes.save("bad", "10.0.0.5", 8000, "ab" * 32, "not-a-token", "ana")
    for address, port, fingerprint, message in (
        ("bad host!", 8000, "ab" * 32, "Invalid address"),
        ("10.0.0.5", 0, "ab" * 32, "Invalid port"),
        ("10.0.0.5", 8000, "abc", "64 hexadecimal"),
    ):
        with pytest.raises(backup_remotes.RemoteError, match=message):
            backup_remotes.save("x", address, port, fingerprint, source["token"], "ana")
    remote = _remote(source)
    assert remote["fingerprint"] == "ab" * 32 and "token" not in str(remote).replace("token_", "")
    backup_stores.create("offsite", "remote", copy_of="site-a:main")
    with pytest.raises(backup_remotes.RemoteError, match="copied into offsite"):
        backup_remotes.remove("site-a")


def test_the_api_adds_a_remote_and_lists_its_stores(client, auth_headers, source, wire, monkeypatch):
    from app.core import peer_https

    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "cd" * 32)  # no certificate in the test's directories
    admin = auth_headers("ana")
    body = {"name": "site-a", "address": "10.0.0.5", "port": 8000, "fingerprint": "ab" * 32, "token": source["token"]}
    r = client.post("/backup-remotes", json=body, headers=admin)
    assert r.status_code == 201 and "token" not in r.json()
    assert client.get("/backup-remotes", headers=admin).json()["items"][0]["name"] == "site-a"
    assert client.get("/backup-remotes/site-a/stores", headers=admin).json()["items"] == [
        {"name": "main", "encrypted": True}
    ]
    assert len(client.get("/backup-remotes/this-node", headers=admin).json()["fingerprint"]) == 64
    viewer = {"Authorization": f"Bearer {source['token']}"}
    assert client.get("/backup-remotes", headers=viewer).status_code == 403


def test_the_reader_role_is_assigned_on_a_store(client, auth_headers, make_user, pools):
    admin = auth_headers("ana")
    backup_stores.create("main", "local")
    make_user("copier2", "viewer", "correct horse battery")
    body = {
        "subject_type": "user",
        "subject_id": "copier2",
        "role": "store_reader",
        "resource_type": "backup_store",
        "resource_id": "main",
    }
    assert client.post("/acl", json=body, headers=admin).status_code == 201
    assert permissions.has_scoped_privilege(
        {"username": "copier2", "role": "viewer"}, "backup_store", "main", "backup_store.read"
    )
    assert not permissions.has_scoped_privilege(
        {"username": "copier2", "role": "viewer"}, "backup_store", "other", "backup_store.read"
    )
