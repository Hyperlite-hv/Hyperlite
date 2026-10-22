"""SMB/CIFS pools (app/core/smb_storage.py): the pool definition libvirt mounts, the credentials file written root-only
on each node and removed with the pool, the checks before anything is written, and the password kept out of every
answer. The mount itself (libvirt's options reaching mount.cifs, a Samba share requiring a password, files owned by
QEMU) was checked on a real Linux 6.8 client against Samba 4.19."""

import json
import os
import stat
import xml.etree.ElementTree as ET

import pytest

from app.core import paths, smb_storage
from app.routers import storage
from tests.test_shared_pools import _Host

FS = "{http://libvirt.org/schemas/storagepool/fs/1.0}"
SMB = {
    "name": "nas",
    "type": "smb",
    "smb_host": "192.168.1.30",
    "smb_share": "VM Disks",
    "smb_user": "vmstore",
    "smb_password": "S3cr&t, p@ss!",
    "smb_domain": "LAB",
}


def _options(xml):
    return [o.get("name") for o in ET.fromstring(xml).iter(f"{FS}option")]


def test_the_pool_is_a_netfs_cifs_pool_naming_a_credentials_file():
    payload = storage.PoolCreate(**SMB)
    xml = storage._build_pool_xml(payload, "/var/lib/libvirt/hyperlite-pools/nas", "/etc/hyperlite/priv/smb/x.cred")
    root = ET.fromstring(xml)
    assert root.get("type") == "netfs"
    assert root.find("source/host").get("name") == "192.168.1.30"
    assert root.find("source/dir").get("path") == "VM Disks"
    assert root.find("source/format").get("type") == "cifs"
    assert _options(xml) == [
        "credentials=/etc/hyperlite/priv/smb/x.cred",
        "uid=libvirt-qemu",
        "gid=kvm",
        "forceuid",
        "forcegid",
        "file_mode=0660",
        "dir_mode=0770",
    ]
    assert "S3cr&t" not in xml and "vmstore" not in xml, "no credential in the definition"
    assert smb_storage.is_smb(root) and smb_storage.version_of(root) == "default"
    assert smb_storage.credentials_of(root) == "/etc/hyperlite/priv/smb/x.cred"


def test_a_fixed_version_is_passed_to_mount():
    payload = storage.PoolCreate(**SMB, smb_version="3.0")
    xml = storage._build_pool_xml(payload, "/p", "/etc/hyperlite/priv/smb/x.cred")
    assert _options(xml)[1] == "vers=3.0"
    assert smb_storage.version_of(ET.fromstring(xml)) == "3.0"


def test_only_a_credentials_file_hyperlite_writes_is_ever_removed():
    def root(path):
        return ET.fromstring(
            "<pool type='netfs'><source><format type='cifs'/></source>"
            f'<mount_opts xmlns="http://libvirt.org/schemas/storagepool/fs/1.0"><option name="credentials={path}"/>'
            "</mount_opts></pool>"
        )

    assert smb_storage.credentials_of(root("/etc/passwd")) is None
    assert smb_storage.credentials_of(root("/etc/hyperlite/priv/smb/../env.cred")) is None
    assert smb_storage.credentials_of(root(f"{paths.PRIV}/smb/a.cred")) == f"{paths.PRIV}/smb/a.cred"


@pytest.fixture(autouse=True)
def _no_credentials_left():
    """paths.PRIV is one directory for the whole test session."""
    import shutil

    shutil.rmtree(paths.PRIV / "smb", ignore_errors=True)
    yield
    shutil.rmtree(paths.PRIV / "smb", ignore_errors=True)


@pytest.fixture()
def hosts(database, monkeypatch):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, added_at) VALUES ('n2', 'n2.lan', 'root', 22, 'now')"
        )
        db.commit()
    hosts = {"local": _Host(), "n2": _Host()}
    monkeypatch.setattr(storage, "open_conn", lambda node=None: hosts[node or "local"])
    monkeypatch.setattr(storage.shutil, "which", lambda name: f"/sbin/{name}")
    monkeypatch.setattr(storage.smb_storage, "check", lambda path: {"ok": True, "message": None})
    sent = []

    def fake_ssh(node, command, input_text=None):
        sent.append((node, command, input_text))

    monkeypatch.setattr(smb_storage, "_node_ssh", fake_ssh)
    hosts["ssh"] = sent
    return hosts


def test_a_pool_on_this_host_writes_its_credentials_root_only(client, auth_headers, hosts):
    r = client.post("/storage", json=SMB, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    cred = paths.PRIV / "smb" / "nas.cred"
    assert cred.read_text() == "username=vmstore\npassword=S3cr&t, p@ss!\ndomain=LAB\n"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert stat.S_IMODE(cred.parent.stat().st_mode) == 0o700
    assert f"credentials={cred}" in _options(hosts["local"].pools["nas"].xml)
    assert "S3cr&t" not in r.text


def test_a_pool_on_another_node_writes_there_through_ssh_input(client, auth_headers, hosts):
    r = client.post("/storage?node=n2", json=SMB, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    ((node, command, text),) = hosts["ssh"]
    assert node == "n2" and "S3cr&t" not in command, "the password never goes on a command line"
    assert "umask 077" in command and "/etc/hyperlite/priv/smb/nas.cred" in command
    assert text.startswith("username=vmstore\npassword=S3cr&t, p@ss!\n")
    assert "credentials=/etc/hyperlite/priv/smb/nas.cred" in _options(hosts["n2"].pools["nas"].xml)


def test_a_share_that_does_not_mount_leaves_no_credentials_behind(client, auth_headers, hosts):
    hosts["local"].reachable = False
    r = client.post("/storage", json=SMB, headers=auth_headers("root"))
    assert r.status_code == 502 and "Could not mount the SMB share" in r.json()["detail"]
    assert not (paths.PRIV / "smb" / "nas.cred").exists()
    assert "nas" not in hosts["local"].pools


def test_removing_the_pool_removes_its_credentials(client, auth_headers, hosts):
    headers = auth_headers("root")
    client.post("/storage", json=SMB, headers=headers)
    cred = paths.PRIV / "smb" / "nas.cred"
    assert cred.exists()
    assert client.delete("/storage/nas?confirm=true", headers=headers).status_code == 200
    assert not cred.exists()


def test_the_listing_says_smb_with_its_version(client, auth_headers, hosts):
    headers = auth_headers("root")
    client.post("/storage", json={**SMB, "smb_version": "3.1.1"}, headers=headers)
    summary = storage._pool_summary(hosts["local"].pools["nas"])
    assert (summary["type"], summary["protocol"], summary["smb_version"], summary["nfs_version"]) == (
        "netfs",
        "smb",
        "3.1.1",
        None,
    )


def test_a_shared_pool_keeps_its_password_encrypted_for_later_nodes(client, auth_headers, hosts, database):
    from app.core import shared_pools

    headers = auth_headers("root")
    r = client.post("/storage", json={**SMB, "all_nodes": True}, headers=headers)
    assert {x["node"]: x["state"] for x in r.json()["results"]} == {"local": "created", "n2": "created"}
    with database.get_conn() as db:
        stored = json.loads(db.execute("SELECT definition FROM shared_pools").fetchone()[0])
    assert stored["smb_password"] != SMB["smb_password"] and stored["smb_user"] == "vmstore"
    assert "S3cr&t" not in json.dumps(client.get("/storage/shared", headers=headers).json())
    assert shared_pools.for_every_node()[0]["definition"]["smb_password"] == SMB["smb_password"]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"smb_share": "vms/sub"}, "share name"),
        ({"smb_share": ""}, "share name"),
        ({"smb_host": "nas;rm -rf /"}, "server address"),
        ({"smb_user": ""}, "user name"),
        ({"smb_password": "two\nlines"}, "one line"),
        ({"smb_password": ""}, "password is required"),
        ({"smb_domain": "LAB'"}, "domain"),
    ],
)
def test_invalid_fields_are_refused_before_anything_is_written(client, auth_headers, hosts, change, said):
    r = client.post("/storage", json={**SMB, **change}, headers=auth_headers("root"))
    assert r.status_code == 422 and said in r.json()["detail"]
    assert not os.path.exists(paths.PRIV / "smb" / "nas.cred")
    assert hosts["local"].pools == {}


def test_without_the_smb_client_the_cause_is_named(client, auth_headers, hosts, monkeypatch):
    monkeypatch.setattr(storage.shutil, "which", lambda name: None)
    r = client.post("/storage", json=SMB, headers=auth_headers("root"))
    assert r.status_code == 422 and "cifs-utils" in r.json()["detail"]
    assert client.get("/storage/support", headers=auth_headers("eve", role="viewer")).json()["smb"] == "no_client"


def test_the_check_writes_a_probe_and_reads_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(smb_storage.nfs_permissions, "qemu_identity", lambda: (os.getuid(), 0, "me:root"))
    assert smb_storage.check(str(tmp_path)) == {"ok": True, "message": None}
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(smb_storage.nfs_permissions, "qemu_identity", lambda: (os.getuid() + 1, 0, "qemu:kvm"))
    assert "not by QEMU" in smb_storage.check(str(tmp_path))["message"]
    assert "cannot write" in smb_storage.check(str(tmp_path / "missing"))["message"]
