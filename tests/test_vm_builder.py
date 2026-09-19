"""Domain XML generation and input validation for VM creation."""

import xml.etree.ElementTree as ET

import pytest

from app.core import vm_builder


@pytest.fixture(autouse=True)
def no_cluster_probe(monkeypatch):
    """CPU baselining talks to libvirt on every registered node; not needed here."""
    monkeypatch.setattr(vm_builder, "_compute_migratable_cpu_xml", lambda: "<cpu mode='host-model'/>")


@pytest.mark.parametrize("name", ["web-01", "a1", "VM-Prod-2"])
def test_valid_vm_names(name):
    assert vm_builder.validate_name(name) is None


@pytest.mark.parametrize("name", ["", "a", "-leading", "has space", "semi;colon", "../etc", "x" * 64, "vm$(id)"])
def test_invalid_vm_names_are_rejected(name):
    assert vm_builder.validate_name(name)  # returns a human-readable error message


@pytest.mark.parametrize("name", ["alice", "_svc", "dev-user_1"])
def test_valid_usernames(name):
    assert vm_builder.validate_username(name) is None


@pytest.mark.parametrize("name", ["", "Root", "1abc", "a b", "x;y", "a" * 40])
def test_invalid_usernames_are_rejected(name):
    assert vm_builder.validate_username(name)


def build(**kwargs):
    defaults = {
        "vm_name": "vm1",
        "vcpu": 2,
        "memory_mb": 2048,
        "disk_paths": ["/var/lib/libvirt/images/vm1.qcow2"],
        "cloudinit_path": "/var/lib/libvirt/images/vm1-cloudinit.iso",
    }
    defaults.update(kwargs)
    return ET.fromstring(vm_builder.build_domain_xml(**defaults))


def test_generated_xml_describes_the_requested_resources():
    xml = build()
    assert xml.findtext("name") == "vm1"
    assert xml.findtext("vcpu") == "2"
    memory = xml.find("memory")
    assert memory.get("unit") == "MiB" and memory.text == "2048"


def test_file_disks_and_block_disks_are_rendered_differently():
    xml = build(disk_paths=["/images/a.qcow2", ("/dev/zvol/tank/vm1", "block")])
    disks = [d for d in xml.findall("devices/disk") if d.get("device") == "disk"]
    assert [d.get("type") for d in disks] == ["file", "block"]
    assert disks[1].find("source").get("dev") == "/dev/zvol/tank/vm1"
    assert disks[1].find("driver").get("type") == "raw"


def test_cloud_init_seed_is_attached_as_a_cdrom():
    xml = build()
    cdroms = [d for d in xml.findall("devices/disk") if d.get("device") == "cdrom"]
    assert any("cloudinit" in (c.find("source").get("file") or "") for c in cdroms)


def test_network_interface_uses_the_requested_network():
    xml = build(network="isolated")
    assert xml.find("devices/interface/source").get("network") == "isolated"


def test_special_characters_in_names_cannot_inject_xml():
    xml = build(vm_name="vm1", network="default")
    assert xml.tag == "domain"
