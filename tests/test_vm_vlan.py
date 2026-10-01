"""VLAN tags on VM interfaces: accepted only on a network that can carry them (Open vSwitch, SR-IOV). libvirt refuses
to start a VM whose interface has a tag on any other network, so the API refuses the tag before writing anything."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import network_edit

NETWORKS = {
    "nat": "<network><name>nat</name><forward mode='nat'/><bridge name='virbr0'/></network>",
    "isolated": "<network><name>isolated</name><bridge name='virbr1'/></network>",
    "linux-br": "<network><name>linux-br</name><forward mode='bridge'/><bridge name='br0'/></network>",
    "macvtap": "<network><name>macvtap</name><forward mode='bridge'><interface dev='eno1'/></forward></network>",
    "ovs": "<network><name>ovs</name><forward mode='bridge'/><bridge name='ovsbr0'/>"
    "<virtualport type='openvswitch'/></network>",
    "ovs-portgroup": "<network><name>ovs-portgroup</name><forward mode='bridge'/><bridge name='ovsbr0'/>"
    "<portgroup name='p'><virtualport type='openvswitch'/></portgroup></network>",
    "sriov": "<network><name>sriov</name><forward mode='hostdev' managed='yes'><pf dev='eno2'/></forward></network>",
    "passthrough": "<network><name>passthrough</name><forward mode='passthrough'><interface dev='eno3'/></forward>"
    "</network>",
}


@pytest.mark.parametrize(
    ("name", "carries"),
    [
        ("nat", False),
        ("isolated", False),
        ("linux-br", False),
        ("macvtap", False),
        ("ovs", True),
        ("ovs-portgroup", True),
        ("sriov", True),
        ("passthrough", True),
    ],
)
def test_only_open_vswitch_and_sriov_networks_carry_tags(name, carries):
    assert network_edit.carries_vlan_tags(ET.fromstring(NETWORKS[name])) is carries


class _Net:
    def __init__(self, name):
        self._name = name

    def XMLDesc(self, flags=0):
        return NETWORKS[self._name]


class _Domain:
    def __init__(self):
        self.xml = (
            "<domain><name>web</name><devices><interface type='network'><mac address='52:54:00:00:00:01'/>"
            "<source network='nat'/><model type='virtio'/></interface></devices></domain>"
        )
        self.attached = []
        self.updated = []

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return 0

    def attachDeviceFlags(self, xml, flags):
        self.attached.append(xml)

    def updateDeviceFlags(self, xml, flags):
        self.updated.append(xml)


class _Conn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        if name != "web":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def networkLookupByName(self, name):
        if name not in NETWORKS:
            raise libvirt.libvirtError("no network")
        return _Net(name)

    def close(self):
        pass


@pytest.fixture()
def domain(monkeypatch):
    from app.routers.vms import devices

    dom = _Domain()
    monkeypatch.setattr(devices, "open_conn", lambda *a, **k: _Conn(dom))
    return dom


@pytest.mark.parametrize("network", ["nat", "linux-br", "macvtap"])
def test_a_tag_on_a_network_that_cannot_carry_it_is_refused_before_any_change(client, auth_headers, domain, network):
    admin = auth_headers("admin")
    r = client.post("/vms/web/interfaces", json={"network": network, "vlan_tag": 20}, headers=admin)
    assert r.status_code == 422 and "cannot carry a VLAN tag" in r.json()["detail"]
    r = client.put("/vms/web/network", json={"network": network, "vlan_tag": 20}, headers=admin)
    assert r.status_code == 422 and f"Network '{network}'" in r.json()["detail"]
    assert domain.attached == [] and domain.updated == []


def test_a_tag_on_open_vswitch_is_written_and_no_tag_is_fine_anywhere(client, auth_headers, domain):
    admin = auth_headers("admin")
    assert client.post("/vms/web/interfaces", json={"network": "ovs", "vlan_tag": 20}, headers=admin).status_code == 201
    assert ET.fromstring(domain.attached[-1]).find("vlan/tag").get("id") == "20"
    assert client.post("/vms/web/interfaces", json={"network": "nat"}, headers=admin).status_code == 201
    assert ET.fromstring(domain.attached[-1]).find("vlan") is None
    # Moving an interface without a tag drops a tag left by an older version, so the VM can start again.
    domain.xml = domain.xml.replace("<model", "<vlan><tag id='20'/></vlan><model")
    assert client.put("/vms/web/network", json={"network": "nat"}, headers=admin).status_code == 200
    assert ET.fromstring(domain.updated[-1]).find("vlan") is None


def test_a_missing_network_is_still_a_404(client, auth_headers, domain):
    r = client.post("/vms/web/interfaces", json={"network": "nope", "vlan_tag": 20}, headers=auth_headers("admin"))
    assert r.status_code == 404
