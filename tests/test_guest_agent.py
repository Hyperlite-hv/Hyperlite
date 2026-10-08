"""Hyperlite Tools (qemu-guest-agent): state read from the channel, clean shutdown and reboot through the agent with
an ACPI fallback, the guest's own IP address, a quiesced hot-backup snapshot, and the agent in cloud-init."""

import subprocess
from pathlib import Path

import libvirt
import pytest

from app.core import guest_agent, vm_builder
from tests.conftest import agent_answer

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

    def UUIDString(self):
        return f"uuid-{id(self)}"

    def agent_reply(self, command):
        assert "guest-network-get-interfaces" in command
        self._agent("addresses")
        return agent_answer(self.addrs)

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


def test_the_ip_is_the_guests_first_routable_ipv4(fake_agent):
    addrs = {
        "lo": [("ipv4", "127.0.0.1")],
        "eth0": [("ipv6", "fe80::1"), ("ipv4", "169.254.3.4")],
        "eth1": [("ipv4", "192.0.2.15")],
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


def test_a_hung_agent_never_holds_the_vm_list(fake_agent):
    """On a test node one hung agent held the VM list for over 13 minutes: libvirt waits for an agent without limit."""
    import threading
    import time

    release = threading.Event()

    class Hung(FakeDomain):
        asked = 0

        def agent_reply(self, command):
            Hung.asked += 1
            release.wait(5)
            return agent_answer({"eth0": [("ipv4", "192.0.2.77")]})

    hung, fine = Hung(), FakeDomain(addrs={"eth0": [("ipv4", "192.0.2.15")]})
    t = time.monotonic()
    ips = guest_agent.ipv4_many([hung, fine], wait=0.3)
    assert time.monotonic() - t < 1
    assert ips == {hung.UUIDString(): None, fine.UUIDString(): "192.0.2.15"}
    # Asked again while the first question is still in flight: neither asked twice nor waited for.
    t = time.monotonic()
    guest_agent.ipv4_many([hung], wait=2)
    assert Hung.asked == 1 and time.monotonic() - t < 0.5
    # Once it answers, the next list has its address.
    release.set()
    deadline = time.monotonic() + 3
    while guest_agent.ipv4_many([hung], wait=0.2)[hung.UUIDString()] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert guest_agent.ipv4_many([hung], wait=0.2)[hung.UUIDString()] == "192.0.2.77"


def test_an_agent_that_did_not_answer_is_left_alone_for_a_while(fake_agent, monkeypatch):
    asked = []

    class Silent(FakeDomain):
        def agent_reply(self, command):
            asked.append(1)
            raise libvirt.libvirtError("agent not responding")

    vm = Silent()
    assert guest_agent.ipv4_many([vm], wait=1) == {vm.UUIDString(): None}
    assert guest_agent.ipv4_many([vm], wait=1) == {vm.UUIDString(): None}
    assert len(asked) == 1  # not asked again within RETRY_AFTER_S
    monkeypatch.setattr(guest_agent, "RETRY_AFTER_S", 0)
    guest_agent._silent_until.clear()
    guest_agent.ipv4_many([vm], wait=1)
    assert len(asked) == 2
