"""The VM list reads each VM's XML once, its state in one bulk call, the DHCP leases once per network and the side
data (SSH user, OS label) in one query per table: about ten calls per VM made 1,000 VMs take 4 s."""

import libvirt

from app.core.vm_meta import set_vm_os_label, set_vm_ssh_user
from app.routers.vms import _shared
from tests.conftest import agent_answer

RUNNING_XML = """<domain type='kvm' id='3'><name>{name}</name><uuid>{uuid}</uuid>
<memory unit='KiB'>2097152</memory><vcpu placement='static' current='2'>4</vcpu>
<os firmware='efi'><type>hvm</type><loader type='pflash' secure='yes'/></os>
<devices>
  <interface type='network'><mac address='52:54:00:AA:00:01'/><source network='default'/></interface>
  <channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0' state='{agent}'/></channel>
</devices></domain>"""

STOPPED_XML = """<domain type='kvm'><name>{name}</name><uuid>{uuid}</uuid>
<memory unit='KiB'>1048576</memory><vcpu>1</vcpu><os><type>hvm</type></os>
<devices><disk type='block' device='disk'><source dev='/dev/zvol/tank/{name}-disk0'/></disk></devices></domain>"""


class FakeDomain:
    def __init__(self, name, xml, running, agent_ip=None):
        self._name, self._xml, self._running, self._agent_ip = name, xml, running, agent_ip
        self.xml_reads = 0

    def name(self):
        return self._name

    def ID(self):
        return 3 if self._running else -1

    def isActive(self):
        return self._running

    def UUIDString(self):
        return f"uuid-{self._name}"

    def XMLDesc(self, flags=0):
        self.xml_reads += 1
        return self._xml

    def state(self):
        return [libvirt.VIR_DOMAIN_RUNNING if self._running else libvirt.VIR_DOMAIN_SHUTOFF, 1]

    def agent_reply(self, command):
        return agent_answer({"eth0": [("ipv4", self._agent_ip)]})

    def connect(self):
        return self.conn


class FakeNetwork:
    def name(self):
        return "default"

    def DHCPLeases(self):
        return [{"mac": "52:54:00:aa:00:01", "type": libvirt.VIR_IP_ADDR_TYPE_IPV4, "ipaddr": "192.0.2.10"}]


class FakeConn:
    def __init__(self, domains):
        self.domains = domains
        self.lease_reads = 0
        for d in domains:
            d.conn = self

    def getAllDomainStats(self, stats):
        assert stats == libvirt.VIR_DOMAIN_STATS_STATE
        return [(d, {"state.state": d.state()[0]}) for d in self.domains]

    def listAllNetworks(self, flags):
        self.lease_reads += 1
        return [FakeNetwork()]


def make_conn():
    web = FakeDomain("web", RUNNING_XML.format(name="web", uuid="u1", agent="disconnected"), running=True)
    db = FakeDomain("db", RUNNING_XML.format(name="db", uuid="u2", agent="connected"), True, "198.51.100.7")
    old = FakeDomain("old", STOPPED_XML.format(name="old", uuid="u3"), running=False)
    return FakeConn([web, db, old])


def test_the_list_reads_each_xml_once_and_the_leases_once(database, fake_agent):
    set_vm_ssh_user("web", "antho")
    set_vm_os_label("old", "Debian 13")
    conn = make_conn()
    by_name = {v["nom"]: v for v in _shared._domain_summaries(conn)}

    assert [d.xml_reads for d in conn.domains] == [1, 1, 1]
    assert conn.lease_reads == 1
    web, db, old = by_name["web"], by_name["db"], by_name["old"]
    assert web["ip"] == "192.0.2.10" and web["agent_invite"] == "inactif" and web["utilisateur_ssh"] == "antho"
    assert web["vcpu"] == 2 and web["memoire_mo"] == 2048.0 and web["firmware"] == "uefi_secure"
    assert db["ip"] == "198.51.100.7" and db["agent_invite"] == "actif"
    assert old["etat"] == "arrete" and old["ip"] is None and old["agent_invite"] is None and old["id"] is None
    assert old["os"] == "Debian 13" and old["stockage_zfs"] is True and old["vcpu"] == 1


def test_one_vm_gets_the_same_summary_as_in_the_list(database, monkeypatch, fake_agent):
    monkeypatch.setattr(_shared, "get_vm_uptime_s", lambda name: 42)
    conn = make_conn()
    listed = {v["nom"]: v for v in _shared._domain_summaries(conn)}
    for domain in conn.domains:
        assert _shared._domain_summary(domain) == listed[domain.name()]


def test_a_vm_undefined_while_listing_is_left_out(database):
    conn = make_conn()

    def gone(flags=0):
        raise libvirt.libvirtError("Domain not found")

    conn.domains[0].XMLDesc = gone
    assert [v["nom"] for v in _shared._domain_summaries(conn)] == ["db", "old"]
