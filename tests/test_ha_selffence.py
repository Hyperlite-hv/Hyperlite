"""The HA watchdog (app/core/ha_selffence.py): armed only while this node runs HA VMs in a quorate cluster, fed while
it has the quorum, starved without it (the node resets), disarmed cleanly otherwise. The device is a file here: the
host's own watchdog is never touched (tests/conftest.py points HYPERLITE_WATCHDOG_DEVICE elsewhere)."""

import pytest

from app.core import cluster_lead, ha_selffence


@pytest.fixture()
def node(tmp_path, monkeypatch):
    dev = tmp_path / "watchdog"
    dev.write_bytes(b"")
    monkeypatch.setenv("HYPERLITE_WATCHDOG_DEVICE", str(dev))
    world = {"mode": "cluster", "reason": None, "ha_vm": True, "published": []}
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
    assert ha_selffence.report()["arme"] and node["published"] == [True]
    assert ha_selffence.report()["reinitialise_sous_s"] == 125  # two expiries (iTCO) plus a feed period
    ha_selffence.tick()
    assert written(node) == b"\0\0"  # fed at each round


def test_without_the_quorum_it_is_starved_never_disarmed_and_fed_again_once_back(node):
    ha_selffence.tick()
    node["reason"] = "This node has lost the cluster's quorum"
    ha_selffence.tick()
    ha_selffence.tick()
    assert written(node) == b"\0"  # not fed: the node resets after the timeout
    assert ha_selffence.report()["arme"] and ha_selffence.report()["sans_quorum"]
    assert b"V" not in written(node)  # never disarmed while cut off
    node["reason"] = None
    ha_selffence.tick()
    assert written(node) == b"\0\0" and not ha_selffence.report()["sans_quorum"]


def test_disarmed_cleanly_when_no_ha_vm_runs_here_any_more(node):
    ha_selffence.tick()
    node["ha_vm"] = False
    ha_selffence.tick()
    assert written(node).endswith(b"V")  # the magic close: the watchdog stops instead of resetting
    assert not ha_selffence.report()["arme"] and node["published"] == [True, False]


def test_a_normal_stop_disarms_it(node):
    ha_selffence.tick()
    ha_selffence.stop()
    assert written(node).endswith(b"V") and not ha_selffence.report()["arme"]


def test_libvirt_not_answering_keeps_it_armed_and_fed(node):
    ha_selffence.tick()
    node["ha_vm"] = RuntimeError("libvirt does not answer")
    ha_selffence.tick()
    assert ha_selffence.report()["arme"] and written(node) == b"\0\0"


def test_never_armed_outside_a_cluster_or_without_ha_vm(node):
    node["mode"] = "local"  # a single node, as serveur-antho
    ha_selffence.tick()
    assert not ha_selffence.report()["arme"] and written(node) == b""
    node["mode"], node["ha_vm"] = "cluster", False
    ha_selffence.tick()
    assert not ha_selffence.report()["arme"] and node["published"] == []


def test_never_armed_without_the_quorum(node):
    node["reason"] = "This node has lost the cluster's quorum"
    ha_selffence.tick()
    assert not ha_selffence.report()["arme"]


def test_a_missing_device_is_reported(node, monkeypatch):
    monkeypatch.setenv("HYPERLITE_WATCHDOG_DEVICE", str(node["dev"].parent / "absent"))
    ha_selffence.tick()
    report = ha_selffence.report()
    assert not report["arme"] and "could not be armed" in report["probleme"]


def test_turned_off_by_the_environment(node, monkeypatch):
    monkeypatch.setenv("HYPERLITE_WATCHDOG", "0")
    ha_selffence.tick()
    assert not ha_selffence.report()["arme"] and written(node) == b""


def test_the_suite_never_points_at_the_hosts_watchdog():
    assert str(ha_selffence.device()) != "/dev/watchdog"
