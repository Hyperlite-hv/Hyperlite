"""NFS pools: whether QEMU can own its disk files there, and a clear reason when a VM cannot open one."""

import os

import pytest


@pytest.fixture()
def qemu(monkeypatch, tmp_path):
    from app.core import nfs_permissions

    conf = tmp_path / "qemu.conf"
    conf.write_text('# user = "root"\nuser = "+64055"\ngroup = "+993"\n')
    monkeypatch.setattr(nfs_permissions, "QEMU_CONF", conf)
    return nfs_permissions


def test_qemus_identity_comes_from_qemu_conf(qemu):
    assert qemu.qemu_identity() == (64055, 993, "+64055:+993")


def test_a_share_where_files_can_be_given_to_qemu_passes(qemu, tmp_path, monkeypatch):
    real_chown = os.chown
    monkeypatch.setattr(qemu.os, "chown", lambda p, u, g: real_chown(p, u, g) if os.geteuid() == 0 else None)
    if os.geteuid() != 0:
        # without root, emulate all_squash,anonuid=<qemu>: the server records QEMU's user as owner
        monkeypatch.setattr(qemu, "qemu_identity", lambda: (os.getuid(), os.getgid(), "me:me"))
    result = qemu.check(tmp_path, export="/srv/vms")
    assert result == {"ok": True, "message": None}
    assert not list(tmp_path.glob(".hyperlite-permission-check-*"))  # the probe file is removed


def test_root_squash_is_detected_and_the_fix_is_given(qemu, tmp_path, monkeypatch):
    def squashed(path, uid, gid):
        raise PermissionError(1, "Operation not permitted")  # what root_squash answers to chown

    monkeypatch.setattr(qemu.os, "chown", squashed)
    monkeypatch.setattr(qemu, "qemu_identity", lambda: (64055, 993, "libvirt-qemu:kvm"))
    result = qemu.check(tmp_path, export="/srv/vms")
    assert result["ok"] is False
    msg = result["message"]
    assert "root_squash" in msg and "libvirt-qemu:kvm" in msg
    assert "/srv/vms <network>(rw,sync,no_subtree_check,all_squash,anonuid=64055,anongid=993)" in msg
    assert "exportfs -ra" in msg and "chown 64055:993" in msg
    assert not list(tmp_path.glob(".hyperlite-permission-check-*"))


def test_a_share_this_host_cannot_write_to_is_reported(qemu, tmp_path):
    missing = tmp_path / "not-mounted"
    result = qemu.check(missing)
    assert result["ok"] is False and "cannot even write" in result["message"]


def test_a_denied_disk_on_nfs_gets_the_real_reason(qemu, tmp_path, monkeypatch):
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "/dev/sda1 / ext4 rw 0 0\n"
        "192.168.3.10:/srv/vms /var/lib/libvirt/hyperlite-pools/nfs-shared nfs4 rw,vers=4.2 0 0\n"
    )
    monkeypatch.setattr(qemu, "qemu_identity", lambda: (64055, 993, "libvirt-qemu:kvm"))
    qemu_error = (
        "internal error: process exited while connecting to monitor: qemu-system-x86_64: -blockdev "
        '{"driver":"file","filename":"/var/lib/libvirt/hyperlite-pools/nfs-shared/test.qcow2"}: '
        "Could not open '/var/lib/libvirt/hyperlite-pools/nfs-shared/test.qcow2': Permission denied"
    )
    text = qemu.explain_denied_disk(qemu_error, mounts_file=mounts)
    assert "192.168.3.10:/srv/vms" in text and "root_squash" in text and "anonuid=64055" in text
    assert "'/srv/vms <network>" in text  # the export path of this share, not a placeholder
    # a disk on a local filesystem, or another error, is left to the generic message
    assert (
        qemu.explain_denied_disk(qemu_error.replace("hyperlite-pools/nfs-shared", "images"), mounts_file=mounts) is None
    )
    assert qemu.explain_denied_disk("No space left on device", mounts_file=mounts) is None


def test_describe_exception_uses_it(qemu, monkeypatch):
    from app.core import error_messages

    monkeypatch.setattr(qemu, "explain_denied_disk", lambda message, mounts_file="/proc/mounts": "NFS-REASON")
    text = error_messages.describe_exception(RuntimeError("Could not open '/x.qcow2': Permission denied"))
    assert text.startswith("NFS-REASON : ") and "Could not open" in text


class _Pool:
    def __init__(self, kind, active=True):
        self.kind, self.active = kind, active

    def XMLDesc(self, flags=0):
        return (
            f"<pool type='{self.kind}'><name>p</name><source><host name='nas'/><dir path='/srv/vms'/></source>"
            "<target><path>/var/lib/libvirt/hyperlite-pools/p</path></target></pool>"
        )

    def isActive(self):
        return self.active


class _Conn:
    def __init__(self):
        self.pools = {"nas": _Pool("netfs"), "local": _Pool("dir"), "off": _Pool("netfs", active=False)}

    def storagePoolLookupByName(self, name):
        import libvirt

        if name not in self.pools:
            raise libvirt.libvirtError("no pool")
        return self.pools[name]

    def close(self):
        pass


def test_an_existing_nfs_pool_can_be_checked(database, client, auth_headers, monkeypatch):
    from app.core import nfs_permissions
    from app.routers import storage

    calls = []
    monkeypatch.setattr(storage, "open_conn", lambda node=None: _Conn())
    monkeypatch.setattr(
        nfs_permissions,
        "check",
        lambda path, export="/srv/share": calls.append((path, export)) or {"ok": False, "message": "root_squash…"},
    )
    admin = auth_headers("admin")
    r = client.post("/storage/nas/check-permissions", headers=admin)
    assert r.status_code == 200 and r.json() == {"nom": "nas", "ok": False, "message": "root_squash…"}
    assert calls == [("/var/lib/libvirt/hyperlite-pools/p", "/srv/vms")]
    assert client.post("/storage/local/check-permissions", headers=admin).status_code == 422
    assert client.post("/storage/off/check-permissions", headers=admin).status_code == 409
    assert client.post("/storage/ghost/check-permissions", headers=admin).status_code == 404
    assert (
        client.post("/storage/nas/check-permissions", headers=auth_headers("viewer", role="observateur")).status_code
        == 403
    )
