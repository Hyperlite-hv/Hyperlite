"""Encrypted backup stores (docs/design/backup-store.md, section 6; step 3): a backup's chunks and files unreadable
without the store's key, which the cluster keeps encrypted in its secrets and shows once; a master recovery key that
reads a backup again with nothing else; taking over an encrypted store with its key; and the recovery tool."""

import json
import uuid

import pytest

from app.core import backup_store, backup_store_recover, backup_store_vm, backup_stores
from app.core.database import get_conn

VM = str(uuid.uuid4())
DISK = (b"secret disk data " * 4000)[:70000]


@pytest.fixture()
def pools(tmp_path, monkeypatch):
    found = {"nas": tmp_path / "nas"}
    found["nas"].mkdir()
    monkeypatch.setattr(backup_stores, "_pool_dir", lambda name: found[name])
    return found


def _backup(store, stamp="t1", extra=None):
    index = store.write_disk(lambda off, n: DISK[off : off + n], len(DISK))
    files = {"vm-config.json": b'{"vcpu": 2}', "nvram": b"NVRAM-SECRET"}
    return store.write_backup(VM, stamp, {"vda": index}, {"vm": "web", **(extra or {})}, files)


def test_a_backups_files_are_encrypted_and_bound_to_their_backup(tmp_path):
    store = backup_store.create(tmp_path / "s", encrypted_with=backup_store.new_key())
    first, second = _backup(store, "t1"), _backup(store, "t2")
    assert b"NVRAM-SECRET" not in (first / "nvram").read_bytes()
    assert store.read_file(VM, "t1", "nvram") == b"NVRAM-SECRET"
    assert json.loads((first / "manifest.json").read_text())["files"] == {
        "vm-config.json": {"encrypted": True},
        "nvram": {"encrypted": True},
    }
    (second / "nvram").write_bytes((first / "vm-config.json").read_bytes())  # one backup's file passed off as another
    with pytest.raises(backup_store.DamagedChunk, match="authentication failed"):
        store.read_file(VM, "t2", "nvram")
    plain = backup_store.create(tmp_path / "p")
    _backup(plain)
    assert plain.read_file(VM, "t1", "nvram") == b"NVRAM-SECRET"


def test_the_master_key_reads_a_backup_with_nothing_else(tmp_path):
    key = backup_store.new_key()
    private, public = backup_store.new_master_key()
    store = backup_store.create(tmp_path / "s", encrypted_with=key)
    directory = _backup(store, extra={"recovery_key": store.sealed_key(public)})
    manifest = json.loads((directory / "manifest.json").read_text())
    assert backup_store.recover_key(manifest, private) == key
    other, _ = backup_store.new_master_key()
    with pytest.raises(backup_store.StoreError, match="not the master key"):
        backup_store.recover_key(manifest, other)
    with pytest.raises(backup_store.StoreError, match="without a master"):
        backup_store.recover_key({}, private)


def test_a_key_code_round_trips_and_a_wrong_one_is_refused():
    key = backup_store.new_key()
    code = backup_store.key_code(key)
    assert all(len(group) <= 4 for group in code.split("-"))
    assert backup_store.key_from_code(code.lower().replace("-", " ")) == key
    for bad in ("", "ABCD-EFGH", "not a key at all !!"):
        with pytest.raises(backup_store.StoreError):
            backup_store.key_from_code(bad)


def test_a_master_public_key_is_checked():
    _, public = backup_store.new_master_key()
    assert backup_store.check_public_key(public.decode()).startswith(b"-----BEGIN PUBLIC KEY-----")
    with pytest.raises(backup_store.StoreError, match="PEM"):
        backup_store.check_public_key("hello")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    for weak in (rsa.generate_private_key(65537, 2048), ec.generate_private_key(ec.SECP256R1())):
        pem = weak.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with pytest.raises(backup_store.StoreError, match="3072"):
            backup_store.check_public_key(pem)


def test_an_encrypted_store_keeps_its_key_in_the_cluster_secrets_only(database, pools):
    row, code = backup_stores.create("vault", "nas", encrypted=True)
    assert row["encrypted"] == 1 and code
    with get_conn() as conn:
        sealed = conn.execute("SELECT key_encrypted FROM backup_store_keys WHERE name = 'vault'").fetchone()[0]
    assert code not in sealed  # encrypted at rest
    assert code not in json.dumps(dict(row)) and "key" not in (
        pools["nas"] / "hyperlite-store" / ".store.json"
    ).read_text().replace("key_fingerprint", "")
    store = backup_stores.open_store("vault")
    _backup(store)
    found = backup_store_vm.store_backup_of(store.backup_dir(VM, "t1"))
    with backup_store_vm.files_of(found) as files:  # what a restore reads, decrypted
        assert (files / "nvram").read_bytes() == b"NVRAM-SECRET"
    assert backup_store_vm.verify(store.backup_dir(VM, "t1"))[0] == "verified"
    (store.backup_dir(VM, "t1") / "nvram").write_bytes(b"HLBF1" + b"\0" * 40)
    status, problems, _ = backup_store_vm.verify(store.backup_dir(VM, "t1"))
    assert status == "corrupted" and any("nvram" in p for p in problems)
    with get_conn() as conn:
        conn.execute("DELETE FROM backup_store_keys")
        conn.commit()
    with pytest.raises(backup_stores.StoreError, match="key of the backup store 'vault' is missing"):
        backup_stores.open_store("vault")


def test_an_encrypted_store_is_taken_over_with_its_key_only(database, pools):
    key = backup_store.new_key()
    backup_store.create(pools["nas"] / "old", encrypted_with=key)  # left by another cluster
    with pytest.raises(backup_stores.StoreError, match="give its key"):
        backup_stores.create("old", "nas", "old")
    with pytest.raises(backup_stores.StoreError, match="not the store's key"):
        backup_stores.create("old", "nas", "old", key=backup_store.key_code(backup_store.new_key()))
    row, code = backup_stores.create("old", "nas", "old", key=backup_store.key_code(key))
    assert row["encrypted"] == 1 and code is None  # nothing new to show
    assert backup_stores.open_store("old").encrypted


def test_a_master_key_needs_an_encrypted_store(database, pools):
    _, public = backup_store.new_master_key()
    with pytest.raises(backup_stores.StoreError, match="for an encrypted store"):
        backup_stores.create("plain", "nas", master_public_key=public.decode())


def test_the_api_shows_the_keys_once(client, auth_headers, pools):
    admin = auth_headers("ana")
    r = client.post(
        "/backup-stores",
        json={"name": "vault", "pool": "nas", "encrypted": True, "master_key": "generate"},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["encrypted"] and body["master_key"]
    assert body["key_code"] and body["key_qr_svg"].lstrip().startswith("<")
    assert body["master_private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    listed = client.get("/backup-stores/vault", headers=admin).json()
    assert not {"key_code", "key_qr_svg", "master_private_key"} & set(listed)
    r = client.post(
        "/backup-stores", json={"name": "x", "pool": "nas", "encrypted": True, "master_key": "own"}, headers=admin
    )
    assert r.status_code == 422


def test_the_recovery_tool_reads_a_disk_and_a_file_back(database, pools, tmp_path, monkeypatch):
    private, public = backup_store.new_master_key()
    _, code = backup_stores.create("vault", "nas", encrypted=True, master_public_key=public.decode())
    store = backup_stores.open_store("vault")
    _backup(store, extra=backup_stores.manifest_extra("vault"))
    master = tmp_path / "master.pem"
    master.write_bytes(private)
    out = tmp_path / "vda.raw"
    root = str(store.root)
    assert backup_store_recover.main(["disk", root, VM, "t1", "vda", str(out), "--master-key", str(master)]) == 0
    assert out.read_bytes() == DISK
    assert (
        backup_store_recover.main(["file", root, VM, "t1", "vm-config.json", str(tmp_path / "c"), "--key-code", code])
        == 0
    )
    assert (tmp_path / "c").read_bytes() == b'{"vcpu": 2}'
    assert backup_store_recover.main(["disk", root, VM, "t1", "vda", str(out)]) == 1  # encrypted: a key is needed
    assert backup_store_recover.main(["list", root]) == 0
