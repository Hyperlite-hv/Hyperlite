"""Pool types the host cannot create are explained before and during the attempt, and without the ZFS kernel
module no zpool/zfs command runs at all (each one made the kernel retry loading it, logging an error)."""

import subprocess

import pytest

from app.core import zfs_storage


@pytest.fixture()
def zfs_host(monkeypatch, tmp_path):
    """A host with the ZFS binaries; returns setters for the module and Secure Boot state."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/sbin/{name}")
    module = tmp_path / "module-zfs"
    efivar = tmp_path / "SecureBoot"
    monkeypatch.setattr(zfs_storage, "ZFS_MODULE", module)
    monkeypatch.setattr(zfs_storage, "SECURE_BOOT_VAR", efivar)
    built = tmp_path / "modules" / "6.12.9" / "updates" / "dkms"
    built.mkdir(parents=True)
    (built / "zfs.ko.xz").write_bytes(b"")
    monkeypatch.setattr(zfs_storage, "MODULES", tmp_path / "modules")
    monkeypatch.setattr("os.uname", lambda: type("U", (), {"release": "6.12.9"})())

    def state(loaded, secure_boot):
        if loaded:
            module.mkdir(exist_ok=True)
        efivar.write_bytes(b"\x06\x00\x00\x00" + (b"\x01" if secure_boot else b"\x00"))

    return state


def test_status_explains_why_zfs_is_unusable(zfs_host, monkeypatch):
    zfs_host(loaded=False, secure_boot=True)
    assert zfs_storage.status() == "secure_boot"
    zfs_host(loaded=False, secure_boot=False)
    assert zfs_storage.status() == "module_not_loaded"
    zfs_host(loaded=True, secure_boot=True)
    assert zfs_storage.status() == "ok"
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert zfs_storage.status() == "not_installed"


def test_a_kernel_updated_without_its_headers_is_said(zfs_host, monkeypatch, tmp_path):
    """Seen on a node: the kernel was updated without linux-headers-amd64, DKMS built no zfs module for it, and ZFS
    stopped working. Said as such, with the package that fixes it."""
    zfs_host(loaded=False, secure_boot=True)
    monkeypatch.setattr("os.uname", lambda: type("U", (), {"release": "6.12.13"})())
    (tmp_path / "modules" / "6.12.13").mkdir()
    assert zfs_storage.status() == "module_not_built"
    assert "linux-headers-amd64" in zfs_storage.STATUS_MESSAGES["module_not_built"]


def test_no_zpool_call_without_the_module(zfs_host, monkeypatch):
    zfs_host(loaded=False, secure_boot=True)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    assert zfs_storage.list_pools() == []
    with pytest.raises(zfs_storage.ZfsError, match="Secure Boot"):
        zfs_storage._run("zpool", "list")
    assert calls == []


def test_load_module_runs_modprobe_once(zfs_host, monkeypatch):
    zfs_host(loaded=False, secure_boot=True)
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a, 1, "", "")
    )
    assert zfs_storage.load_module() is False
    assert calls == [["modprobe", "zfs"]]


def test_support_endpoint_and_creation_errors(client, auth_headers, zfs_host, monkeypatch):
    zfs_host(loaded=False, secure_boot=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    real_which = {"mount.nfs": None}
    monkeypatch.setattr("shutil.which", lambda name: real_which.get(name, f"/usr/sbin/{name}"))
    headers = auth_headers("root", "admin")
    support = client.get("/storage/support", headers=headers).json()
    assert support["nfs"] == "no_client" and support["zfs"] == "secure_boot" and support["iscsi"] == "ok"

    zfs = client.post("/storage", headers=headers, json={"name": "tank", "type": "zfs", "size_gb": 5})
    assert zfs.status_code == 422 and "mokutil --import" in zfs.json()["detail"]
    nfs = client.post(
        "/storage",
        headers=headers,
        json={"name": "share", "type": "netfs", "nfs_host": "192.168.1.10", "nfs_export_path": "/srv/share"},
    )
    assert nfs.status_code == 422 and "nfs-common" in nfs.json()["detail"]
