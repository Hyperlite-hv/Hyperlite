"""Automatic HA in a cluster (docs/design/ha-automatic.md, 4, #333 step 4): the leader restarts the HA VMs of a lost
member once it is fenced, by priority, where they fit, at most 3 times an hour; the node that comes back undefines
them. Everything around (members, statuses, BMC, watchdog, libvirt) is faked."""

import json
from datetime import UTC, datetime, timedelta

import libvirt
import pytest
from fastapi import HTTPException

from app.core import cluster_lead, ha, ha_fencing, ha_recovery, ha_selffence, maintenance, self_node, statd

UUID = "6f1c2a3e-0000-4000-8000-00000000000{}"


def xml(vm, memory_mib=1024, n=1):
    return f"<domain><name>{vm}</name><uuid>{UUID.format(n)}</uuid><memory unit='MiB'>{memory_mib}</memory></domain>"


def protect(
    database, vm, node="n2", memory_mib=1024, n=1, priority="medium", preferred=(), restarts=(), at="2026-01-01"
):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) VALUES (?, ?, ?, 'admin', ?)",
            (vm, node, xml(vm, memory_mib, n), at),
        )
        db.execute(
            "INSERT INTO ha_policies (vm_name, priority, preferred_nodes, auto_restarts) VALUES (?, ?, ?, ?)",
            (vm, priority, json.dumps(list(preferred)), json.dumps(list(restarts))),
        )
        db.commit()


@pytest.fixture()
def cluster(database, monkeypatch):
    """Three members: n1 (this node, the leader), n2, n3. Each test says how old n2's status is and how it is fenced."""
    world = {
        "ages": {"n1": 1.0, "n2": 1.0, "n3": 1.0},
        "free": {"n1": 2000, "n2": 8000, "n3": 6000},
        "fencing": None,  # n2's fence method
        "power_off": {"fenced": True, "detail": "off"},
        "watchdog": None,  # what n2 published
        "started": [],
        "refuse": [],  # libvirt errors to raise, in order, on start
        "maintenance": set(),
    }
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: True)
    monkeypatch.setattr(cluster_lead, "read_only_reason", lambda: None)
    monkeypatch.setattr(ha_recovery, "_members", lambda: ["n2", "n3"])
    monkeypatch.setattr(statd, "ages", lambda: dict(world["ages"]))
    # The members' last statuses: None for a member means none is held (every HA VM of it counts).
    world["last_vms"] = {}
    monkeypatch.setattr(statd, "latest", lambda: {n: (world["ages"][n], vms) for n, vms in world["last_vms"].items()})
    monkeypatch.setattr(statd, "table", lambda: {n: {"mem_free_mb": f} for n, f in world["free"].items()})
    monkeypatch.setattr(ha_fencing, "get", lambda node: {"method": world["fencing"]} if world["fencing"] else None)
    monkeypatch.setattr(ha_fencing, "power_off", lambda node: dict(world["power_off"]))
    monkeypatch.setattr(ha_selffence, "published", lambda node: world["watchdog"])
    monkeypatch.setattr(maintenance, "get", lambda node: {"node": node} if node in world["maintenance"] else None)
    monkeypatch.setattr(ha_recovery, "_notify", lambda *a, **k: None)

    world["unreachable"] = set()  # nodes a connection to fails (open_conn's HTTPException)
    world["running"] = set()  # (vm, node) a libvirt lookup finds running

    def start_on(row, target, reuse_stopped=False):
        assert reuse_stopped
        if target in world["unreachable"]:
            raise HTTPException(status_code=502, detail="Node unreachable: timed out")
        if world["refuse"]:
            raise libvirt.libvirtError(world["refuse"].pop(0))
        world["started"].append((row["vm_name"], target))
        ha._store().sync_definition(row["vm_name"], row["domain_xml"], "now", node=target)
        return []

    monkeypatch.setattr(ha, "start_on", start_on)
    monkeypatch.setattr(ha, "forget_on", lambda row, target: None)
    monkeypatch.setattr(ha, "runs_on", lambda row, node: (row["vm_name"], node) in world["running"])
    monkeypatch.setattr(
        ha, "adopt", lambda row, node: ha._store().sync_definition(row["vm_name"], row["domain_xml"], "now", node=node)
    )
    ha_recovery._lost.clear()
    yield world
    ha_recovery._lost.clear()


def state(vm):
    return ha.get_protected(vm)["ha_state"]


def test_a_member_seen_recently_is_left_alone(cluster, database):
    protect(database, "web")
    assert ha_recovery.tick() == {"n2": "ok"} and cluster["started"] == []


def test_a_lost_member_whose_watchdog_has_not_reset_it_yet_is_waited_for(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 70.0  # lost, seen 70 s ago
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    assert ha_recovery.tick() == {"n2": "fencing"}
    assert state("web") == "fencing" and cluster["started"] == []


def test_once_its_watchdog_reset_it_the_vm_restarts_on_the_member_with_the_most_free_memory(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 200.0  # seen 200 s ago: past 120 s + 30 s
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]  # n3: 6000 MiB free, this node 2000
    row = ha.get_protected("web")
    assert row["node"] == "n3" and row["ha_state"] == "ok" and len(ha.policy("web")["auto_restarts"]) == 1


def test_a_member_its_bmc_powered_off_is_recovered_at_once(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]


def test_nothing_proving_the_member_stopped_leaves_its_vms_to_an_administrator(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 600.0
    assert ha_recovery.tick() == {"n2": "manual"}
    assert state("web") == "manual" and cluster["started"] == []
    assert "nothing proves" in ha.get_protected("web")["last_action"]


def test_a_bmc_that_did_not_confirm_falls_back_to_the_watchdog(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 200.0
    cluster["fencing"] = "ipmi"
    cluster["power_off"] = {"fenced": False, "detail": "no answer"}
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]


def test_high_priority_first_and_a_preferred_node_before_free_memory(cluster, database):
    protect(database, "low", priority="low", n=1, at="2026-01-01")
    protect(database, "high", priority="high", n=2, at="2026-01-02", preferred=["n1"])
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    ha_recovery.tick()
    assert cluster["started"] == [("high", "n1"), ("low", "n3")]


def test_a_member_without_room_or_in_maintenance_is_no_target(cluster, database):
    protect(database, "big", memory_mib=7000)
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["maintenance"] = {"n3"}
    ha_recovery.tick()
    assert cluster["started"] == [] and state("big") == "manual"  # n3 in maintenance, n1 has 2000 MiB


def test_three_restarts_within_an_hour_leave_the_vm_to_an_administrator(cluster, database):
    recent = [(datetime.now(UTC) - timedelta(minutes=m)).isoformat() for m in (5, 20, 40)]
    protect(database, "web", restarts=recent)
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    ha_recovery.tick()
    assert cluster["started"] == [] and state("web") == "manual"


def test_a_disk_still_locked_by_the_old_node_is_tried_again_at_the_next_round(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["refuse"] = ["internal error: Is another process using the image [/mnt/share/web.qcow2]?"]
    ha_recovery.tick()
    assert cluster["started"] == [] and state("web") == "recovering"
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")] and state("web") == "ok"


def test_only_the_leader_with_the_quorum_acts(cluster, database, monkeypatch):
    protect(database, "web")
    cluster["ages"]["n2"] = 600.0
    cluster["fencing"] = "ipmi"
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: False)
    assert ha_recovery.tick() == {} and cluster["started"] == []
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: True)
    monkeypatch.setattr(cluster_lead, "read_only_reason", lambda: "no quorum")
    assert ha_recovery.tick() == {} and cluster["started"] == []


def test_a_member_back_with_its_vm_running_after_a_network_cut_keeps_it(cluster, database, monkeypatch):
    protect(database, "web")
    cluster["ages"]["n2"] = 70.0
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    ha_recovery.tick()
    cluster["ages"]["n2"] = 2.0
    monkeypatch.setattr(ha_recovery, "_running_on", lambda member: {"web"})
    assert ha_recovery.tick() == {"n2": "ok"} and state("web") == "ok" and cluster["started"] == []


def test_a_member_back_after_a_reset_its_vm_did_not_survive_gets_it_restarted(cluster, database, monkeypatch):
    """Its watchdog reset it, and it rebooted before the leader's wait was over: its VM runs nowhere."""
    protect(database, "web")
    cluster["ages"]["n2"] = 70.0
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    ha_recovery.tick()
    cluster["ages"]["n2"] = 2.0
    monkeypatch.setattr(ha_recovery, "_running_on", lambda member: set())
    assert ha_recovery.tick() == {"n2": "recovering"}
    assert cluster["started"] == [("web", "n3")] and state("web") == "ok"  # elsewhere, though n2 has more room


def test_the_node_that_comes_back_undefines_the_vms_that_moved(database, monkeypatch):
    protect(database, "moved", node="n3", n=1)
    protect(database, "running", node="n3", n=2)
    protect(database, "mine", node="n1", n=3)
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    undefined = []

    class Domain:
        def __init__(self, name, n, active):
            self.name, self.n, self.active = name, n, active

        def isActive(self):
            return self.active

        def UUIDString(self):
            return UUID.format(self.n)

        def undefineFlags(self, flags):
            undefined.append(self.name)

    domains = {
        "moved": Domain("moved", 1, False),
        "running": Domain("running", 2, True),
        "mine": Domain("mine", 3, False),
    }

    class Conn:
        def lookupByName(self, name):
            return domains[name]

        def close(self):
            pass

    monkeypatch.setattr(ha, "open_conn", lambda key=None: Conn())
    assert ha.forget_moved() == ["moved"] and undefined == ["moved"]
    assert ha.moved_away("moved") and not ha.moved_away("mine")


def test_memory_is_read_from_the_definition():
    assert ha_recovery.memory_mib(xml("x", 4096)) == 4096
    assert ha_recovery.memory_mib("<domain><memory>2097152</memory></domain>") == 2048  # KiB by default
    assert ha_recovery.memory_mib("not xml") == 0


def test_a_recovered_vm_restarts_with_its_cd_rom_empty():
    """Its installation ISO is in the lost node's own library: left in the drive, the VM started nowhere else (lab)."""
    definition = (
        "<domain><name>web</name><devices>"
        "<disk type='file' device='disk'><source file='/mnt/share/web.qcow2'/><target dev='vda'/></disk>"
        "<disk type='file' device='cdrom'><source file='/var/lib/hyperlite/template/iso/debian.iso'/><target dev='sda'/></disk>"
        "</devices></domain>"
    )
    emptied, ejected = ha.eject_media(definition)
    assert ejected == ["/var/lib/hyperlite/template/iso/debian.iso"]
    assert "debian.iso" not in emptied and "/mnt/share/web.qcow2" in emptied and 'device="cdrom"' in emptied
    assert ha.eject_media("<domain><devices/></domain>") == ("<domain><devices/></domain>", [])


def test_the_recovered_vm_gets_its_networks_started_and_its_settings_moved(database, monkeypatch):
    """As a migration does: a network of the VM stopped on the target is started; its tags, notes and start at boot
    follow it."""
    from app.core import object_meta, vm_boot

    protect(database, "web", node="n2")
    row = dict(
        ha.get_protected("web"),
        domain_xml=xml("web").replace(
            "</domain>", "<devices><interface type='network'><source network='lan'/></interface></devices></domain>"
        ),
    )
    started, moved = [], []

    class Net:
        active = False

        def isActive(self):
            return self.active

        def create(self):
            started.append("lan")

        def setAutostart(self, on):
            pass

    class Domain:
        def setAutostart(self, on):
            pass

        def create(self):
            pass

        def XMLDesc(self, flags=0):
            return row["domain_xml"]

    class Conn:
        def lookupByName(self, name):
            raise libvirt.libvirtError("Domain not found")

        def networkLookupByName(self, name):
            return Net()

        def defineXML(self, definition):
            return Domain()

        def close(self):
            pass

    monkeypatch.setattr(ha, "open_conn", lambda key=None: Conn())
    monkeypatch.setattr(object_meta, "follow_migration", lambda vm, src, dst: moved.append(("meta", src, dst)))
    monkeypatch.setattr(vm_boot, "follow_migration", lambda vm, src, dst: moved.append(("boot", src, dst)))
    ha.start_on(row, "n3", reuse_stopped=True)
    assert started == ["lan"] and moved == [("meta", "n2", "n3"), ("boot", "n2", "n3")]


def test_a_vm_stopped_on_purpose_before_its_node_was_lost_stays_stopped(cluster, database):
    """As Proxmox's requested state: only what ran on the member when it was last seen is restarted."""
    protect(database, "web", n=1)
    protect(database, "idle", n=2)
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["last_vms"]["n2"] = [{"name": "web", "state": "active"}, {"name": "idle", "state": "stopped"}]
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]
    assert ha.get_protected("idle")["node"] == "n2" and "not restarted" in ha.get_protected("idle")["last_action"]


def test_back_after_a_reset_only_what_ran_before_is_restarted(cluster, database, monkeypatch):
    protect(database, "web", n=1)
    protect(database, "idle", n=2)
    cluster["ages"]["n2"] = 70.0
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    cluster["last_vms"]["n2"] = [{"name": "web", "state": "active"}, {"name": "idle", "state": "stopped"}]
    ha_recovery.tick()  # lost: what ran is taken from its last status now
    cluster["ages"]["n2"] = 2.0
    cluster["last_vms"]["n2"] = []  # rebooted: nothing runs
    monkeypatch.setattr(ha_recovery, "_running_on", lambda member: set())
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]


# --- Found by a review of #333 step 4 (ECC's code-reviewer, in a sandbox) ---------------------------------------------


def test_a_target_that_cannot_be_reached_is_passed_over_for_the_next(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["unreachable"] = {"n3"}  # its HTTPException used to end the round: the VM stayed recovering for ever
    protect(database, "db", node="n3", n=2)  # another member's VM: the round goes on to it
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n1")] and state("web") == "ok"


def test_a_vm_an_earlier_round_started_is_found_where_it_runs_never_started_twice(cluster, database):
    protect(database, "web")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["running"] = {("web", "n3")}  # started there, its record not updated (the cfs refused the write)
    ha_recovery.tick()
    assert cluster["started"] == []
    row = ha.get_protected("web")
    assert row["node"] == "n3" and row["ha_state"] == "ok"


def test_each_vm_started_takes_its_memory_out_for_the_next(cluster, database):
    for i, vm in enumerate(("a", "b", "c"), start=1):
        protect(database, vm, memory_mib=3000, n=i, at=f"2026-01-0{i}")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    ha_recovery.tick()
    # n3 has 6000 MiB free: room for two of them, not three (all three went there, 9000 MiB on 6000).
    assert cluster["started"] == [("a", "n3"), ("b", "n3")] and state("c") == "manual"


def test_a_bmc_that_did_not_confirm_is_asked_again_before_the_vms_go_to_manual(cluster, database, monkeypatch):
    protect(database, "web")
    cluster["ages"]["n2"] = 600.0
    cluster["fencing"] = "ipmi"
    monkeypatch.setattr(ha_recovery, "BMC_RETRY_S", 0)
    answers = [{"fenced": False, "detail": "busy"}, {"fenced": True, "detail": "off"}]
    monkeypatch.setattr(ha_fencing, "power_off", lambda node: answers.pop(0))
    assert ha_recovery.tick() == {"n2": "fencing"} and state("web") == "fencing"  # not manual after one refusal
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]


def test_a_bmc_that_never_confirms_leaves_the_vms_to_an_administrator(cluster, database, monkeypatch):
    protect(database, "web")
    cluster["ages"]["n2"] = 600.0
    cluster["fencing"] = "ipmi"
    cluster["power_off"] = {"fenced": False, "detail": "no answer"}
    monkeypatch.setattr(ha_recovery, "BMC_RETRY_S", 0)
    calls = []
    monkeypatch.setattr(ha_fencing, "power_off", lambda node: calls.append(node) or dict(cluster["power_off"]))
    for _ in range(5):
        ha_recovery.tick()
    assert len(calls) == ha_recovery.BMC_ATTEMPTS and state("web") == "manual" and cluster["started"] == []


def test_a_member_whose_service_was_stopped_normally_is_never_fenced(cluster, database, monkeypatch):
    protect(database, "web")
    cluster["ages"]["n2"] = 600.0  # an update that takes long, a service stopped by hand: its VMs still run
    cluster["fencing"] = "ipmi"
    cluster["watchdog"] = {"armed": False, "stopped": True, "reset_within_s": None}
    monkeypatch.setattr(ha_fencing, "power_off", lambda node: pytest.fail("a running node powered off"))
    assert ha_recovery.tick() == {"n2": "manual"} and cluster["started"] == []
    assert "stopped normally" in ha.get_protected("web")["last_action"]


def test_a_leader_that_loses_the_lead_mid_round_restarts_nothing_more(cluster, database, monkeypatch):
    protect(database, "a", n=1, at="2026-01-01")
    protect(database, "b", n=2, at="2026-01-02")
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    leads = iter([True, True, False])  # the round's own check, then one per VM
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: next(leads, False))
    ha_recovery.tick()
    assert cluster["started"] == [("a", "n3")]


# --- Found by a review of the whole code (ECC's python-reviewer, in a sandbox) ---------------------------------------


def test_a_member_back_with_one_vm_running_never_gets_it_started_elsewhere_later(cluster, database, monkeypatch):
    protect(database, "web", n=1)
    protect(database, "db", n=2)
    cluster["ages"]["n2"] = 70.0
    cluster["watchdog"] = {"armed": True, "reset_within_s": 120}
    cluster["last_vms"]["n2"] = [{"name": "web", "state": "active"}, {"name": "db", "state": "active"}]
    ha_recovery.tick()  # lost
    cluster["ages"]["n2"] = 2.0  # back before its watchdog reset it: web ran through, db died
    monkeypatch.setattr(ha_recovery, "_running_on", lambda member: {"web"})
    ha_recovery.tick()
    assert cluster["started"] == [("db", "n3")]
    for _ in range(3):  # every round after used to restart web too, which still runs on n2
        ha_recovery.tick()
    assert cluster["started"] == [("db", "n3")] and ha.get_protected("web")["node"] == "n2"
    assert "n2" not in ha_recovery._lost  # nothing left to recover: it is back


def test_a_vm_stopped_on_purpose_on_a_fenced_member_stays_stopped_once_it_answers(cluster, database):
    protect(database, "web", n=1)
    protect(database, "idle", n=2)
    cluster["ages"]["n2"] = 61.0
    cluster["fencing"] = "ipmi"
    cluster["last_vms"]["n2"] = [{"name": "web", "state": "active"}, {"name": "idle", "state": "stopped"}]
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")]
    cluster["ages"]["n2"] = 2.0  # powered on again by hand
    ha_recovery.tick()
    ha_recovery.tick()
    assert cluster["started"] == [("web", "n3")] and "n2" not in ha_recovery._lost
