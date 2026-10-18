"""The HA watchdog (app/core/ha_selffence.py): armed only while this node runs HA VMs in a quorate cluster, fed while
it has the quorum, starved without it (the node resets), disarmed cleanly otherwise. The device is a file here: the
host's own watchdog is never touched (tests/conftest.py points HYPERLITE_WATCHDOG_DEVICE elsewhere)."""

import json

import pytest

from app.core import cluster_lead, ha_selffence

REAL_PUBLISH = ha_selffence._publish  # the fixture replaces it


@pytest.fixture()
def node(tmp_path, monkeypatch):
    dev = tmp_path / "watchdog"
    dev.write_bytes(b"")
    monkeypatch.setenv("HYPERLITE_WATCHDOG_DEVICE", str(dev))
    world = {"mode": "cluster", "reason": None, "ha_vm": True, "published": [], "timeout": 60}
    monkeypatch.setattr(ha_selffence, "_timeout_of", lambda fd: world["timeout"])  # a file has no driver to ask
    monkeypatch.setattr(cluster_lead, "mode", lambda record=True: world["mode"])
    monkeypatch.setattr(cluster_lead, "read_only_reason", lambda: world["reason"])

    def hosts():
        if isinstance(world["ha_vm"], Exception):
            raise world["ha_vm"]
        return world["ha_vm"]

    monkeypatch.setattr(ha_selffence, "_hosts_running_ha_vm", hosts)
    monkeypatch.setattr(ha_selffence, "_publish", lambda armed: world["published"].append(armed))
    monkeypatch.setattr(ha_selffence, "state", ha_selffence._State())
    world["dev"] = dev
    yield world
    ha_selffence.stop()


def written(world):
    return world["dev"].read_bytes()


def test_armed_and_fed_while_ha_vms_run_in_a_quorate_cluster(node):
    ha_selffence.tick()
    assert ha_selffence.report()["armed"] and node["published"] == [True]
    assert ha_selffence.report()["reset_within_s"] == 125  # two expiries (iTCO) plus a feed period
    ha_selffence.tick()
    assert written(node) == b"\0\0"  # fed at each round


def test_without_the_quorum_it_is_starved_never_disarmed_and_fed_again_once_back(node):
    ha_selffence.tick()
    node["reason"] = "This node has lost the cluster's quorum"
    ha_selffence.tick()
    ha_selffence.tick()
    assert written(node) == b"\0"  # not fed: the node resets after the timeout
    assert ha_selffence.report()["armed"] and ha_selffence.report()["sans_quorum"]
    assert b"V" not in written(node)  # never disarmed while cut off
    node["reason"] = None
    ha_selffence.tick()
    assert written(node) == b"\0\0" and not ha_selffence.report()["sans_quorum"]


def test_disarmed_cleanly_when_no_ha_vm_runs_here_any_more(node):
    ha_selffence.tick()
    node["ha_vm"] = False
    ha_selffence.tick()
    assert written(node).endswith(b"V")  # the magic close: the watchdog stops instead of resetting
    assert not ha_selffence.report()["armed"] and node["published"] == [True, False]


def test_a_normal_stop_disarms_it(node):
    ha_selffence.tick()
    ha_selffence.stop()
    assert written(node).endswith(b"V") and not ha_selffence.report()["armed"]


def test_libvirt_not_answering_keeps_it_armed_and_fed(node):
    ha_selffence.tick()
    node["ha_vm"] = RuntimeError("libvirt does not answer")
    ha_selffence.tick()
    assert ha_selffence.report()["armed"] and written(node) == b"\0\0"


def test_never_armed_outside_a_cluster_or_without_ha_vm(node):
    node["mode"] = "local"  # a single node, as serveur-antho
    ha_selffence.tick()
    assert not ha_selffence.report()["armed"] and written(node) == b""
    node["mode"], node["ha_vm"] = "cluster", False
    ha_selffence.tick()
    assert not ha_selffence.report()["armed"] and node["published"] == []


def test_never_armed_without_the_quorum(node):
    node["reason"] = "This node has lost the cluster's quorum"
    ha_selffence.tick()
    assert not ha_selffence.report()["armed"]


def test_a_missing_device_is_reported(node, monkeypatch):
    monkeypatch.setenv("HYPERLITE_WATCHDOG_DEVICE", str(node["dev"].parent / "absent"))
    ha_selffence.tick()
    report = ha_selffence.report()
    assert not report["armed"] and "could not be armed" in report["problem"]


def test_turned_off_by_the_environment(node, monkeypatch):
    monkeypatch.setenv("HYPERLITE_WATCHDOG", "0")
    ha_selffence.tick()
    assert not ha_selffence.report()["armed"] and written(node) == b""


def test_the_suite_never_points_at_the_hosts_watchdog():
    assert str(ha_selffence.device()) != "/dev/watchdog"


def test_a_driver_that_keeps_its_own_timeout_is_believed_not_the_one_asked(node):
    """The reset time the cluster waits for before restarting this node's VMs elsewhere is the device's: waiting
    125 s for a node whose watchdog fires after 300 s would start its VMs twice."""
    node["timeout"] = 300
    ha_selffence.tick()
    report = ha_selffence.report()
    assert report["armed"] and report["delay_s"] == 300 and report["reset_within_s"] == 605


def test_a_driver_that_does_not_say_still_fences_but_never_vouches_for_a_reset_time(node):
    node["timeout"] = None
    ha_selffence.tick()
    report = ha_selffence.report()
    assert report["armed"] and report["reset_within_s"] is None and "by hand" in report["problem"]


def test_a_timeout_too_short_to_feed_safely_is_not_armed(node):
    node["timeout"] = 10
    ha_selffence.tick()
    report = ha_selffence.report()
    assert not report["armed"] and "too short" in report["problem"]
    assert written(node) == b"V"  # closed the magic way: never left counting down


def test_what_is_published_is_the_devices_reset_time(node, monkeypatch):
    from app.core import self_node
    from app.repositories.cfs import shadow

    puts = []
    monkeypatch.setattr(shadow, "_get_client", lambda: type("C", (), {"put": lambda self, p, d: puts.append(d)})())
    monkeypatch.setattr(self_node, "name", lambda: "hlc1")
    ha_selffence.state.timeout = 300
    REAL_PUBLISH(True)
    entry = json.loads(puts[0])
    assert entry["delay_s"] == 300 and entry["reset_within_s"] == 605
