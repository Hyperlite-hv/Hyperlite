"""VM monitoring of HA VMs (app/core/ha_monitor.py, docs/design/ha-automatic.md 4.9, #333 step 6): a guest that hangs
is reset in place, and nothing else is: not a VM without its agent, not one whose agent was stopped, not a busy one,
not one something else works on, and not more than 3 times an hour."""

import libvirt
import pytest

from app.core import cluster_lead, ha, ha_monitor, maintenance, self_node, vm_locks


class Domain:
    def __init__(self, name="web"):
        self._name = name
        self.agent = "answers"  # "answers", "silent", or "gone" (its channel disconnected)
        self.io = 0
        self.resets = 0
        self.state_ = (libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_RUNNING_BOOTED)
        self.job = libvirt.VIR_DOMAIN_JOB_NONE

    def name(self):
        return self._name

    def state(self):
        return list(self.state_)

    def jobInfo(self):
        return [self.job]

    def reset(self, flags):
        self.resets += 1
        self.agent = "silent"  # booting again: its agent is not up yet


@pytest.fixture()
def world(database, monkeypatch):
    def ping(domain):
        return {"answers": True, "silent": False, "gone": None}[domain.agent]

    monkeypatch.setattr(ha_monitor, "_ping", ping)
    monkeypatch.setattr(ha_monitor, "_io", lambda domain: (domain.io, 0, 0, 0))
    notes = []
    monkeypatch.setattr(ha_monitor, "_notify", lambda title, message, result="failed": notes.append(title))
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) "
            "VALUES ('web', 'local', '<domain/>', 'admin', 'now')"
        )
        db.commit()
    ha.set_monitoring("web", True)
    return {"notes": notes}


def fresh():
    return {"id": 1, "answered": False, "missed": 0, "io": None, "left": None}


def rounds(domain, seen, n):
    for _ in range(n):
        ha_monitor.watch(domain, seen)


def test_a_guest_that_hangs_is_reset_after_three_silent_rounds_without_io(world):
    domain, seen = Domain(), fresh()
    rounds(domain, seen, 1)  # its agent answered: it may be watched
    domain.agent = "silent"
    rounds(domain, seen, 2)
    assert domain.resets == 0
    rounds(domain, seen, 1)
    assert domain.resets == 1 and world["notes"] == ["HA: web reset"]
    assert "reset" in ha.get_protected("web")["last_action"]
    assert ha.get_protected("web")["ha_state"] != "manual"  # monitoring never takes the leader's state
    rounds(domain, seen, 5)  # booting again: its agent must answer before anything counts
    assert domain.resets == 1


def test_a_vm_whose_agent_never_answered_is_never_reset(world):
    domain, seen = Domain(), fresh()
    domain.agent = "silent"
    rounds(domain, seen, 10)
    assert domain.resets == 0


def test_an_agent_stopped_in_the_guest_is_no_hang(world):
    domain, seen = Domain(), fresh()
    rounds(domain, seen, 1)
    domain.agent = "gone"  # its channel disconnected
    rounds(domain, seen, 10)
    assert domain.resets == 0


def test_a_busy_guest_doing_io_is_not_reset(world):
    domain, seen = Domain(), fresh()
    rounds(domain, seen, 1)
    domain.agent = "silent"
    for _ in range(10):
        domain.io += 1  # its disks or network still move: slow, not hung
        rounds(domain, seen, 1)
    assert domain.resets == 0


@pytest.mark.parametrize("busy", ["paused", "job", "lock"])
def test_a_vm_something_else_works_on_is_not_reset(world, busy):
    domain, seen = Domain(), fresh()
    rounds(domain, seen, 1)
    domain.agent = "silent"
    if busy == "paused":
        domain.state_ = (libvirt.VIR_DOMAIN_PAUSED, libvirt.VIR_DOMAIN_PAUSED_USER)
    elif busy == "job":
        domain.job = libvirt.VIR_DOMAIN_JOB_UNBOUNDED  # a migration
    else:
        claim = vm_locks.claim("web", "backup")
    rounds(domain, seen, 10)
    assert domain.resets == 0
    if busy == "lock":
        claim.release()


def test_three_resets_an_hour_then_an_administrator(world):
    domain, seen = Domain(), fresh()
    for _ in range(ha_monitor.RESETS_PER_HOUR + 1):
        domain.agent = "answers"
        rounds(domain, seen, 1)
        domain.agent = "silent"
        rounds(domain, seen, ha_monitor.MISSED_ROUNDS)
    assert domain.resets == ha_monitor.RESETS_PER_HOUR
    assert world["notes"][-1] == "HA: web needs an administrator"
    assert "left to an administrator" in ha.get_protected("web")["last_action"]


def test_only_this_nodes_running_ha_vms_with_monitoring_on_are_watched(world, database, monkeypatch):
    watched = []
    monkeypatch.setattr(ha_monitor, "watch", lambda domain, seen: watched.append(domain.name()))
    monkeypatch.setattr(self_node, "is_self", lambda node: node in (None, "local"))
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(cluster_lead, "read_only_reason", lambda: None)
    with database.get_conn() as db:
        for vm, node in (("off", "local"), ("there", "pve-b")):
            db.execute(
                "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) "
                "VALUES (?, ?, '<domain/>', 'admin', 'now')",
                (vm, node),
            )
        db.commit()
    ha.set_monitoring("there", True)  # on another node: that node watches it

    class Conn:
        def lookupByName(self, name):
            d = Domain(name)
            d.isActive, d.UUIDString, d.ID = (lambda: True), (lambda: f"uuid-{name}"), (lambda: 7)
            return d

        def close(self):
            pass

    monkeypatch.setattr("app.core.libvirt_utils.open_conn", lambda node=None: Conn())
    ha_monitor.tick()
    assert watched == ["web"]
    monkeypatch.setattr(maintenance, "get", lambda node: {"node": node})
    watched.clear()
    ha_monitor.tick()
    assert watched == []  # in maintenance: nothing
    monkeypatch.setattr(maintenance, "get", lambda node: None)
    monkeypatch.setattr(cluster_lead, "read_only_reason", lambda: "no quorum")
    ha_monitor.tick()
    assert watched == []  # without the quorum: nothing


def test_monitoring_is_set_through_the_api_and_off_by_default(client, auth_headers, world):
    headers = auth_headers("root", "admin")
    assert client.get("/ha/web", headers=headers).json()["monitoring"] is True
    r = client.put("/ha/web/monitoring", json={"enabled": False}, headers=headers)
    assert r.status_code == 200 and r.json()["monitoring"] is False
    assert client.put("/ha/nothing/monitoring", json={"enabled": True}, headers=headers).status_code == 404
    assert (
        client.put("/ha/web/monitoring", json={"enabled": True}, headers=auth_headers("eve", "viewer")).status_code
        == 403
    )
    ha.disable_protection("web", "root")
    assert ha.monitoring("web") == {"enabled": False, "resets": []}  # dropped with the protection


def test_what_a_hung_guest_receives_is_no_activity_of_its_own():
    class Counted:
        def __init__(self):
            self.received = 0

        def XMLDesc(self, flags=0):
            return "<domain><devices><disk><target dev='vda'/></disk><interface><target dev='vnet0'/></interface></devices></domain>"

        def blockStats(self, dev):
            return (5, 0, 7, 0, 0)

        def interfaceStats(self, dev):
            # rx_bytes, rx_packets, rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop (the guest's side)
            return (self.received * 60, self.received, 0, 0, 0, 3, 0, 0)

    domain = Counted()
    before = ha_monitor._io(domain)
    domain.received += 50  # the network's broadcasts still reach it
    assert ha_monitor._io(domain) == before == (12, 3)
