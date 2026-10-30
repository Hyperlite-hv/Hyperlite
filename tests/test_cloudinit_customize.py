"""A guest's customisation on its cloud-init drive, as vSphere's and Proxmox VE's: host name and domain, static addresses
by interface, resolvers, a cloud-config of the user's. Written at creation (POST /vms) or later (PUT
/vms/{name}/cloud-init), read back by GET, and refused when it would be wrong rather than written and ignored."""

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from app.core import cloudinit_edit as ce
from app.core import vm_builder, vm_create
from app.routers.vms import create, settings

MACS = ["52:54:00:aa:00:01", "52:54:00:aa:00:02"]


def test_a_customisation_is_checked_and_normalised():
    out = ce.validate_custom(
        {
            "hostname": "Web-1",
            "domain": "Example.Internal.",
            "network": [{"addresses": ["10.0.20.11/24"], "gateway": "10.0.20.1"}],
            "dns": ["10.0.0.53"],
            "dns_search": ["example.internal"],
            "user_data": "packages: [nginx]\nruncmd:\n  - systemctl enable --now nginx\n",
        },
        MACS,
    )
    assert out["hostname"] == "web-1" and out["domain"] == "example.internal"
    assert out["network"] == [{"mac": MACS[0], "addresses": ["10.0.20.11/24"], "gateway": "10.0.20.1"}]
    assert out["dns"] == ["10.0.0.53"] and out["dns_search"] == ["example.internal"]


@pytest.mark.parametrize(
    ("custom", "message"),
    [
        ({"hostname": "web_1"}, "hostname"),
        ({"domain": "bad..domain"}, "domain"),
        ({"network": [{"mac": "52:54:00:ff:ff:ff", "addresses": ["10.0.0.2/24"]}]}, "no interface"),
        ({"network": [{"addresses": ["10.0.0.2"]}]}, "prefix"),
        ({"network": [{"addresses": ["not-an-ip/24"]}]}, "not an address"),
        ({"network": [{"addresses": ["10.0.0.2/24"], "gateway": "10.9.9.1"}]}, "outside"),
        ({"network": [{"addresses": ["10.0.0.2/24"]}, {"mac": MACS[0], "addresses": ["10.0.0.3/24"]}]}, "twice"),
        ({"dns": ["8.8.8"]}, "not an IP"),
        ({"user_data": "- a list"}, "mapping"),
        ({"user_data": "users: []"}, "written by Hyperlite"),
        ({"user_data": "hostname: other"}, "written by Hyperlite"),
        ({"user_data": "a: [unclosed"}, "not valid YAML"),
    ],
)
def test_a_wrong_customisation_is_refused_with_its_reason(custom, message):
    with pytest.raises(ce.CloudInitError, match=message):
        ce.validate_custom(custom, MACS)


def test_the_network_configuration_lists_every_interface():
    custom = ce.validate_custom(
        {
            "network": [{"mac": MACS[1], "addresses": ["10.0.20.11/24", "2001:db8::11/64"], "gateway": "10.0.20.1"}],
            "dns": ["10.0.0.53"],
        },
        MACS,
    )
    net = ce.network_config(custom, MACS)
    assert net["version"] == 2
    first, second = net["ethernets"]["nic0"], net["ethernets"]["nic1"]
    # The interface not configured keeps DHCP (cloud-init leaves an unlisted one down), with the resolvers given.
    assert first["match"] == {"macaddress": MACS[0]} and first["dhcp4"] is True
    assert first["dhcp4-overrides"] == {"use-dns": False}
    assert second["addresses"] == ["10.0.20.11/24", "2001:db8::11/64"]
    assert second["routes"] == [{"to": "0.0.0.0/0", "via": "10.0.20.1"}]
    assert second["nameservers"] == {"addresses": ["10.0.0.53"], "search": []}
    assert ce.network_config(ce.empty_custom(), MACS) is None  # nothing to say: the image's own DHCP


def test_the_user_data_carries_the_host_name_and_the_users_cloud_config():
    custom = ce.validate_custom(
        {"hostname": "web-1", "domain": "example.internal", "user_data": "packages:\n  - nginx\n"}, MACS
    )
    text = ce.user_data("vm1", "debian", None, [], None, custom)
    data = yaml.safe_load(text)
    assert text.startswith("#cloud-config\n")
    assert data["hostname"] == "web-1" and data["fqdn"] == "web-1.example.internal"
    assert data["packages"] == ["nginx"] and data["users"][0]["name"] == "debian"
    assert yaml.safe_load(ce.user_data("vm1", "debian", None, [], None))["hostname"] == "vm1"


@pytest.fixture()
def drive(database, tmp_path, monkeypatch, daemon_libvirt):
    """A VM `web` with two interfaces and a cloud-init drive; cloud-localds records what it is given."""
    iso = tmp_path / "web-cloudinit.iso"
    iso.write_bytes(b"old")
    xml = (
        "<domain><name>web</name><devices>"
        f"<disk type='file' device='cdrom'><source file='{iso}'/><target dev='sda'/></disk>"
        f"<interface type='network'><mac address='{MACS[0]}'/><source network='lan'/></interface>"
        f"<interface type='network'><mac address='{MACS[1]}'/><source network='lan'/></interface>"
        "</devices></domain>"
    )
    domain = Mock()
    domain.XMLDesc.return_value = xml
    domain.isActive.return_value = False
    conn = Mock()
    conn.lookupByName.return_value = domain
    monkeypatch.setattr(settings, "open_conn", lambda *a, **k: conn)
    daemon_libvirt(conn)  # hyperlite-daemon writes the drive, in this process in the tests
    # The drive's directory: vm_builder's, which finds a VM's own drives (and cloudinit_edit's copy of it while the
    # module still imports one), so the test holds whichever lands first.
    monkeypatch.setattr(vm_builder, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(ce, "IMAGES_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ce, "get_or_create_automation_pubkey", lambda: None)
    monkeypatch.setattr(ce, "protect_drive", lambda path: None)
    monkeypatch.setattr(ce.os, "chown", lambda *a: None)
    written = []

    def localds(cmd, **kwargs):
        files = {Path(c).name: Path(c).read_text() for c in cmd[2:] if Path(c).exists()}
        net = [c.split("=", 1)[1] for c in cmd if c.startswith("--network-config=")]
        written.append({"files": files, "network": yaml.safe_load(Path(net[0]).read_text()) if net else None})
        Path(next(c for c in cmd if c.endswith(".new"))).write_bytes(b"new")
        return Mock(returncode=0)

    monkeypatch.setattr(ce.subprocess, "run", localds)
    return written


def test_a_field_left_out_keeps_what_the_drive_holds(client, auth_headers, drive):
    admin = auth_headers("root")
    body = {
        "user": "debian",
        "ssh_keys": [],
        "hostname": "web-1",
        "network": [{"addresses": ["10.0.20.11/24"], "gateway": "10.0.20.1"}],
        "dns": ["10.0.0.53"],
    }
    r = client.put("/vms/web/cloud-init", json=body, headers=admin)
    assert r.status_code == 200, r.text
    assert drive[-1]["network"]["ethernets"]["nic0"]["addresses"] == ["10.0.20.11/24"]
    assert "local-hostname: web-1" in drive[-1]["files"]["meta-data"]

    # What the dashboard sends (the account only): the rest stays.
    r = client.put("/vms/web/cloud-init", json={"user": "debian", "ssh_keys": []}, headers=admin)
    assert r.status_code == 200
    got = client.get("/vms/web/cloud-init", headers=admin).json()
    assert got["hostname"] == "web-1" and got["dns"] == ["10.0.0.53"]
    assert got["network"] == [{"mac": MACS[0], "addresses": ["10.0.20.11/24"], "gateway": "10.0.20.1"}]
    assert drive[-1]["network"]["ethernets"]["nic0"]["addresses"] == ["10.0.20.11/24"]

    # An empty value clears it.
    r = client.put(
        "/vms/web/cloud-init", json={"user": "debian", "network": [], "dns": [], "hostname": ""}, headers=admin
    )
    got = client.get("/vms/web/cloud-init", headers=admin).json()
    assert got["network"] == [] and got["hostname"] is None and drive[-1]["network"] is None


def test_a_wrong_customisation_writes_nothing(client, auth_headers, drive):
    body = {"user": "debian", "network": [{"addresses": ["10.0.20.11/24"], "gateway": "10.9.9.1"}]}
    r = client.put("/vms/web/cloud-init", json=body, headers=auth_headers("root"))
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    assert drive == []


def test_a_creation_checks_the_customisation_before_anything_is_made(database):
    def payload(**fields):
        return create.VMCreate(
            name="web", vcpu=1, memory_mb=512, disks=[{"size_gb": 1}], username="debian", password="pass", **fields
        )

    with pytest.raises(ce.CloudInitError, match="leave mac out"):
        vm_create.customisation(payload(customize={"network": [{"mac": MACS[0], "addresses": ["10.0.0.2/24"]}]}), "m")
    keys, custom = vm_create.customisation(
        payload(ssh_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKey user@host"], customize={"hostname": "web-1"}),
        MACS[0],
    )
    assert keys == ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKey user@host"] and custom["hostname"] == "web-1"


def test_an_iso_installation_takes_no_customisation(client, auth_headers, monkeypatch, tmp_path, daemon_libvirt):
    monkeypatch.setattr(vm_create.disk_library, "ISOS", tmp_path)
    monkeypatch.setattr(create.maintenance, "refuse_if_in_maintenance", lambda *a: None)
    monkeypatch.setattr(vm_create, "validate_vm_resources", lambda *a: [])
    conn = daemon_libvirt(Mock())
    conn.lookupByName.side_effect = vm_create.libvirt.libvirtError("no domain")
    monkeypatch.setattr(vm_create.firmware, "check_choice", lambda conn, choice: None)
    (tmp_path / "debian.iso").touch()
    body = {
        "name": "web",
        "vcpu": 1,
        "memory_mb": 512,
        "disks": [{"size_gb": 1}],
        "iso": "debian.iso",
        "customize": {"hostname": "web-1"},
    }
    r = client.post("/vms", json=body, headers=auth_headers("root"))
    assert r.status_code == 422 and "cloud image" in str(r.json()["detail"])


def test_the_customisation_follows_a_renamed_vm_and_goes_with_a_deleted_one(database):
    from app.core import renaming, vm_records

    ce.save_state("web", "debian", [], {**ce.empty_custom(), "hostname": "web-1"})
    renaming.vm_records("web", "www")
    assert ce.get_custom("www")["hostname"] == "web-1" and ce.get_custom("web") == ce.empty_custom()
    vm_records.forget("www")
    assert ce.get_custom("www") == ce.empty_custom() and ce.get_state("www") is None


def test_the_drive_xml_names_the_interfaces_in_order():
    domain = Mock()
    domain.XMLDesc.return_value = ET.tostring(
        ET.fromstring(
            "<domain><devices>"
            f"<interface type='network'><mac address='{MACS[1].upper()}'/></interface>"
            f"<interface type='network'><mac address='{MACS[0]}'/></interface>"
            "</devices></domain>"
        ),
        encoding="unicode",
    )
    assert ce.interface_macs(domain) == [MACS[1], MACS[0]]
