"""iSCSI pools and VMs on LUNs: the pool XML (with and without CHAP), the checks before anything is written, and the
actions that must refuse a VM on LUNs instead of reporting a success. The real path (a LIO target, a VM booting
from a LUN) was checked on the development machine."""

import json
import subprocess
import xml.etree.ElementTree as ET

import pytest
from fastapi import HTTPException

from app.core import iscsi
from app.routers import storage
from app.routers.vms import create

TARGET = "iqn.2026-09.home.hyperlite:devtest"
LUN0 = "/dev/disk/by-path/ip-192.168.1.20:3260-iscsi-iqn.2026-09.home.hyperlite:devtest-lun-0"


@pytest.mark.parametrize(
    "name",
    ["iqn.2005-10.org.freenas.ctl:vms", "iqn.2004-04.com.qnap:ts-451:iscsi.vm.cb1e2a", TARGET, "eui.02004567A425678D"],
)
def test_real_target_names_are_accepted(name):
    assert iscsi.IQN_RE.match(name)


@pytest.mark.parametrize(
    "name", ["", "vms", "iqn.2005-10.org.freenas.ctl:vms'/><x", "iqn.05-10.org:x", "iqn.2005-10.Org"]
)
def test_malformed_target_names_are_refused(name):
    assert not iscsi.IQN_RE.match(name)


def test_pool_xml_without_and_with_chap():
    plain = storage.PoolCreate(name="nas", type="iscsi", iscsi_host="192.168.1.20", iscsi_target=TARGET)
    root = ET.fromstring(storage._build_pool_xml(plain, iscsi.BY_PATH))
    assert root.get("type") == "iscsi"
    assert root.find("source/host").attrib == {"name": "192.168.1.20", "port": "3260"}
    assert root.find("source/device").get("path") == TARGET
    assert root.find("source/auth") is None
    assert root.findtext("target/path") == "/dev/disk/by-path"

    chap = storage.PoolCreate(
        name="nas", type="iscsi", iscsi_host="nas.home", iscsi_target=TARGET, chap_user="hyper", chap_password="s3cret"
    )
    xml = storage._build_pool_xml(chap, iscsi.BY_PATH)
    auth = ET.fromstring(xml).find("source/auth")
    assert auth.attrib == {"type": "chap", "username": "hyper"}
    assert auth.find("secret").get("usage") == "hyperlite-iscsi-nas"
    assert "s3cret" not in xml  # the password lives in a libvirt secret only
    assert ET.fromstring(iscsi.secret_xml("nas")).get("private") == "yes"


class _Domain:
    def __init__(self, *devs):
        disks = "".join(f"<disk type='block' device='disk'><source dev='{d}'/></disk>" for d in devs)
        self._xml = f"<domain><devices>{disks}<disk type='file' device='disk'><source file='/x.qcow2'/></disk></devices></domain>"

    def XMLDesc(self, *_):
        return self._xml


def test_iscsi_disks_are_told_from_zvols_and_files():
    assert iscsi.iscsi_disks_of_domain(_Domain(LUN0, "/dev/zvol/tank/vm")) == [LUN0]
    assert iscsi.iscsi_disks_of_domain(_Domain("/dev/zvol/tank/vm")) == []
    with pytest.raises(HTTPException) as err:
        iscsi.refuse_if_iscsi(_Domain(LUN0), "A backup")
    assert err.value.status_code == 422 and "iSCSI" in err.value.detail
    iscsi.refuse_if_iscsi(_Domain("/dev/zvol/tank/vm"), "A backup")  # no error


class _Vol:
    def __init__(self, path, capacity):
        self._path, self._capacity = path, capacity

    def path(self):
        return self._path

    def info(self):
        return [0, self._capacity, self._capacity]


class _Pool:
    def __init__(self, vols):
        self.vols = vols

    def refresh(self, _):
        return None

    def storageVolLookupByName(self, name):
        import libvirt

        if name not in self.vols:
            raise libvirt.libvirtError("no volume")
        return self.vols[name]


def test_luns_must_be_named_free_distinct_and_confirmed(monkeypatch):
    pool = _Pool({"unit:0:0:0": _Vol(LUN0, 4 << 30), "unit:0:0:1": _Vol(LUN0[:-1] + "1", 4 << 30)})
    monkeypatch.setattr(create, "get_disk_paths_in_use", lambda conn: {LUN0[:-1] + "1"})
    disks = [
        create.DiskSpec(size_gb=4, lun="unit:0:0:0"),
        create.DiskSpec(size_gb=4),
        create.DiskSpec(size_gb=4, lun="nope"),
        create.DiskSpec(size_gb=4, lun="unit:0:0:1"),
        create.DiskSpec(size_gb=4, lun="unit:0:0:0"),
    ]
    luns, errors = create._resolve_luns(None, pool, disks, erase_luns=False)
    assert luns == [(LUN0, 4 << 30)]
    assert "confirm with erase_luns" in errors[0]
    assert any("Disk 2: choose a LUN" in e for e in errors)
    assert any("'nope' not found" in e for e in errors)
    assert any("Disk 4" in e and "already used by a VM" in e for e in errors)
    assert any("Disk 5" in e and "chosen twice" in e for e in errors)
    _, ok = create._resolve_luns(None, pool, disks[:1], erase_luns=True)
    assert ok == []


def test_a_lun_too_small_for_the_image_is_refused_before_writing(monkeypatch, tmp_path):
    calls = []

    def run(cmd, **_):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"virtual-size": 2 << 30}), "")

    monkeypatch.setattr(create.subprocess, "run", run)
    with pytest.raises(ValueError, match="too small"):
        create._prepare_lun(LUN0, 1 << 30, tmp_path / "base.qcow2")
    assert calls == ["qemu-img"]  # only read, never written
    calls.clear()
    create._prepare_lun(LUN0, 4 << 30, tmp_path / "base.qcow2")
    assert calls == ["qemu-img", "qemu-img"]
    calls.clear()
    create._prepare_lun(LUN0, 4 << 30)  # ISO install or data disk: start wiped
    assert calls == ["dd"]


def test_pool_creation_checks_before_calling_libvirt(client, auth_headers, monkeypatch):
    headers = auth_headers("root", "admin")
    base = {"name": "nas", "type": "iscsi", "iscsi_host": "192.168.1.20"}
    bad = client.post("/storage", headers=headers, json={**base, "iscsi_target": "vms"})
    assert bad.status_code == 422 and "IQN" in bad.json()["detail"]
    chap = client.post("/storage", headers=headers, json={**base, "iscsi_target": TARGET, "chap_user": "hyper"})
    assert chap.status_code == 422 and "CHAP" in chap.json()["detail"]
    monkeypatch.setattr(iscsi.shutil, "which", lambda name: None)
    missing = client.post("/storage", headers=headers, json={**base, "iscsi_target": TARGET})
    assert missing.status_code == 422 and "open-iscsi" in missing.json()["detail"]
