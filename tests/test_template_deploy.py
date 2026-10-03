"""A VM deployed from a template made from a cloud-init VM gets a new cloud-init drive, as a clone does: it booted
with the template's network configuration (netplan bound to the template's MAC address), so without any address,
and with the template's host name and SSH host keys (found on a real host)."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.routers import templates


def _xml(cdrom):
    cd = (
        f"<disk type='file' device='cdrom'><source file='{cdrom}'/><target dev='hdc' bus='ide'/></disk>"
        if cdrom
        else ""
    )
    return (
        "<domain type='kvm'><name>base</name><uuid>u</uuid><os><type>hvm</type></os><devices>"
        "<disk type='file' device='disk'><source file='/t/base.qcow2'/><target dev='vda' bus='virtio'/></disk>"
        f"{cd}<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default'/></interface>"
        "</devices></domain>"
    )


class _Conn:
    def __init__(self):
        self.defined = []

    def lookupByName(self, name):
        raise libvirt.libvirtError("no domain")

    def defineXML(self, xml):
        self.defined.append(xml)
        return type("D", (), {"name": lambda s: "copy"})()

    def close(self):
        pass


@pytest.fixture()
def deploy(database, tmp_path, monkeypatch):
    conn = _Conn()
    seeds = []
    monkeypatch.setattr(templates, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(templates, "open_conn", lambda *a: conn)
    monkeypatch.setattr(templates, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(templates.maintenance, "refuse_if_in_maintenance", lambda *a: None)
    monkeypatch.setattr(templates.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        templates, "create_cloudinit_reseed_iso", lambda name: seeds.append(name) or tmp_path / f"{name}-cloudinit.iso"
    )

    def run(client, admin, cdrom):
        monkeypatch.setattr(
            templates.templates_store, "get_template", lambda n: {"xml": _xml(cdrom), "disk_path": "/t/base.qcow2"}
        )
        r = client.post("/templates/base/deploy", json={"new_name": "copy"}, headers=admin)
        assert r.status_code == 201, r.text
        root = ET.fromstring(conn.defined[-1])
        return [d.find("source").get("file") for d in root.iter("disk") if d.get("device") == "cdrom"]

    return run, seeds, tmp_path


def test_a_cloud_init_template_deploys_with_a_new_cloud_init_drive(deploy, client, auth_headers):
    run, seeds, tmp_path = deploy
    assert run(client, auth_headers("root"), "/i/base-cloudinit.iso") == [str(tmp_path / "copy-cloudinit.iso")]
    assert seeds == ["copy"]


def test_other_templates_deploy_without_any_cd_rom(deploy, client, auth_headers):
    run, seeds, _ = deploy
    admin = auth_headers("root")
    assert run(client, admin, "/isos/debian.iso") == []
    assert run(client, admin, None) == []
    assert seeds == []
