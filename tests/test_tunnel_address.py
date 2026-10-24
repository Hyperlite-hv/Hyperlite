"""The workstation tunnel connects only to an address the host binds to the VM's own network cards
(GHSA-4vfp-w7wf-jv75): the guest agent's word alone could send it anywhere the hypervisor reaches."""

import importlib

import libvirt

tunnel = importlib.import_module("app.routers.vms.tunnel")


class Domain:
    def __init__(self, lease=(), arp=()):
        self.sources = {
            libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE: lease,
            libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_ARP: arp,
        }

    def interfaceAddresses(self, source):
        addrs = [{"type": libvirt.VIR_IP_ADDR_TYPE_IPV4, "addr": a} for a in self.sources[source]]
        return {"vnet0": {"hwaddr": "52:54:00:00:00:01", "addrs": addrs}} if addrs else {}


def test_an_address_only_the_guest_claims_is_never_the_tunnels(monkeypatch):
    monkeypatch.setattr(tunnel.guest_agent, "ipv4", lambda domain: "10.0.0.1")  # the management network's gateway
    assert tunnel._guest_ip(Domain(lease=["192.168.122.50"])) == "192.168.122.50"
    assert tunnel._guest_ip(Domain()) is None


def test_the_agents_address_is_preferred_when_the_host_binds_it_to_the_vm(monkeypatch):
    monkeypatch.setattr(tunnel.guest_agent, "ipv4", lambda domain: "192.168.1.40")  # a static address, bridged
    assert tunnel._guest_ip(Domain(lease=["192.168.122.50"], arp=["192.168.1.40"])) == "192.168.1.40"
