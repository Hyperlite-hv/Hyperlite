"""Hyperlite Tools (qemu-guest-agent): state read from the channel, clean shutdown and reboot through the agent with
an ACPI fallback, the guest's own IP address, a quiesced hot-backup snapshot, and the agent in cloud-init."""

import subprocess
from pathlib import Path

import libvirt
import pytest

from app.core import guest_agent, vm_builder

CHANNEL = "<channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0'{state}/></channel>"


class FakeDomain:
    def __init__(self, active=True, channel="connected", agent_fails=False, addrs=None):
        self.active = active
        self.agent_fails = agent_fails
        self.calls = []
        self.addrs = addrs or {}
        devices = "" if channel is None else CHANNEL.format(state=f" state='{channel}'" if channel else "")
        self._xml = f"<domain><name>vm1</name><devices>{devices}</devices></domain>"

    def name(self):
        return "vm1"

    def isActive(self):
        return self.active

    def XMLDesc(self, *_):
        return self._xml

    def _agent(self, call):
        self.calls.append(call)
        if self.agent_fails:
            raise libvirt.libvirtError("Guest agent is not responding")

    def shutdownFlags(self, flags):
        assert flags == libvirt.VIR_DOMAIN_SHUTDOWN_GUEST_AGENT
        self._agent("agent-shutdown")

    def shutdown(self):
        self.calls.append("acpi-shutdown")

    def reboot(self, flags=0):
        if flags == libvirt.VIR_DOMAIN_REBOOT_GUEST_AGENT:
            self._agent("agent-reboot")
        else:
            self.calls.append("acpi-reboot")

    def interfaceAddresses(self, source):
        assert source == libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT
        self._agent("addresses")
        return self.addrs

    def snapshotCreateXML(self, xml, flags):
        if flags & libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE:
            self._agent("quiesced-snapshot")
        else:
            self.calls.append("snapshot")
        return "snap"


def test_the_state_comes_from_the_channel_without_calling_the_agent():
    assert guest_agent.state(FakeDomain(channel="connected")) == "actif"
    assert guest_agent.state(FakeDomain(channel="disconnected")) == "inactif"
    assert guest_agent.state(FakeDomain(channel="")) == "inactif"
    assert guest_agent.state(FakeDomain(channel=None)) == "non_configure"
    assert guest_agent.state(FakeDomain(active=False)) is None
    assert FakeDomain().calls == []


def test_shutdown_and_reboot_go_through_the_agent_when_it_is_there():
    domain = FakeDomain()
    assert guest_agent.shutdown(domain) == "agent" and guest_agent.reboot(domain) == "agent"
    assert domain.calls == ["agent-shutdown", "agent-reboot"]


@pytest.mark.parametrize("domain", [FakeDomain(channel="disconnected"), FakeDomain(agent_fails=True)])
def test_without_a_working_agent_shutdown_and_reboot_use_acpi(domain):
    assert guest_agent.shutdown(domain) == "acpi" and guest_agent.reboot(domain) == "acpi"
    assert "acpi-shutdown" in domain.calls and "acpi-reboot" in domain.calls


def test_the_ip_is_the_guests_first_routable_ipv4():
    v4, v6 = libvirt.VIR_IP_ADDR_TYPE_IPV4, libvirt.VIR_IP_ADDR_TYPE_IPV6
    addrs = {
        "lo": {"addrs": [{"type": v4, "addr": "127.0.0.1"}]},
        "eth0": {"addrs": [{"type": v6, "addr": "fe80::1"}, {"type": v4, "addr": "169.254.3.4"}]},
        "eth1": {"addrs": [{"type": v4, "addr": "192.0.2.15"}]},
    }
    assert guest_agent.ipv4(FakeDomain(addrs=addrs)) == "192.0.2.15"
    assert guest_agent.ipv4(FakeDomain(addrs={"lo": addrs["lo"]})) is None
    # No agent: the agent is not even asked, the caller falls back to the DHCP lease.
    without = FakeDomain(channel="disconnected", addrs=addrs)
    assert guest_agent.ipv4(without) is None and without.calls == []
    assert guest_agent.ipv4(FakeDomain(agent_fails=True)) is None


def test_the_hot_backup_snapshot_is_quiesced_when_possible_and_still_taken_otherwise():
    flags = libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY
    assert guest_agent.quiesced_snapshot(FakeDomain(), "<x/>", flags) == ("snap", True)
    failing = FakeDomain(agent_fails=True)
    assert guest_agent.quiesced_snapshot(failing, "<x/>", flags) == ("snap", False)
    assert failing.calls == ["quiesced-snapshot", "snapshot"]
    assert guest_agent.quiesced_snapshot(FakeDomain(channel=None), "<x/>", flags) == ("snap", False)


def test_cloud_init_installs_and_starts_the_agent(monkeypatch, tmp_path):
    seen = {}

    def fake_localds(args, **kwargs):
        seen["user_data"] = Path(args[2]).read_text()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(vm_builder.subprocess, "run", fake_localds)
    vm_builder.create_cloudinit_iso("vm1", "tester", "Testpass1", target_dir=tmp_path)
    assert "packages:\n  - qemu-guest-agent\n" in seen["user_data"]
    assert "[systemctl, enable, --now, qemu-guest-agent]" in seen["user_data"]
