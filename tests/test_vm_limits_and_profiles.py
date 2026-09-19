"""VM resource limits, allocation policy and deployment profiles."""

import pytest

from app.core import deployment_profile, vm_limits


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, database):
    for name in (
        "HYPERLITE_ALLOCATION",
        "HYPERLITE_PROFILE",
        "HYPERLITE_VM_MAX_VCPU",
        "HYPERLITE_VM_MAX_MEMORY_MB",
        "HYPERLITE_VM_MAX_DISK_GB",
        "HYPERLITE_VM_MAX_DISKS",
    ):
        monkeypatch.delenv(name, raising=False)
    vm_limits._cache.update(at=0.0, value=None)


@pytest.fixture()
def host(monkeypatch):
    """A 4-core, 16 GiB host with 100 GB of free disk."""
    monkeypatch.setattr(vm_limits.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(deployment_profile.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(vm_limits, "_memory_capabilities_local", lambda: {"totale_mo": 16384})
    monkeypatch.setattr(deployment_profile, "_read_meminfo_mb", lambda: 16384)
    monkeypatch.setattr(vm_limits, "_detect_disk_free_gb", lambda: 100)


def test_recommended_profile_follows_the_hardware(monkeypatch):
    def recommend(cores, mem_mb):
        monkeypatch.setattr(deployment_profile.os, "cpu_count", lambda: cores)
        monkeypatch.setattr(deployment_profile, "_read_meminfo_mb", lambda: mem_mb)
        return deployment_profile.recommend_profile()

    assert recommend(2, 2048) == "homelab"
    assert recommend(4, 32768) == "homelab"  # few cores is enough to be a small host
    assert recommend(8, 16384) == "standard"
    assert recommend(16, 16384) == "avance"
    assert recommend(8, 131072) == "avance"


def test_profile_priority_is_environment_then_admin_choice_then_detection(host, monkeypatch):
    assert deployment_profile.get_active()["source"] == "detecte"
    deployment_profile.set_choice("avance")
    assert deployment_profile.get_active()["actif"] == "avance"
    monkeypatch.setenv("HYPERLITE_PROFILE", "standard")
    active = deployment_profile.get_active()
    assert (active["actif"], active["source"]) == ("standard", "configuration")


def test_unknown_profile_choice_is_rejected(database):
    with pytest.raises(ValueError):
        deployment_profile.set_choice("enterprise")


def test_default_policy_limits_a_vm_to_a_share_of_the_host(host):
    limits = vm_limits.compute_limits(force=True)
    assert limits["vcpu"]["max"] == 4
    assert limits["memoire_mo"]["max"] < 16384  # never the whole host memory
    assert limits["disque_go"]["max"] < 100
    assert limits["politique"]["actif"] == "limites"


def test_overcommit_policy_allows_more_than_the_hardware(host):
    vm_limits.set_policy("surallocation")
    limits = vm_limits.compute_limits(force=True)
    assert limits["vcpu"]["max"] == 16  # 4 cores x 4
    assert limits["memoire_mo"]["max"] > 16384


def test_free_policy_removes_the_artificial_ceilings(host):
    vm_limits.set_policy("libre")
    limits = vm_limits.compute_limits(force=True)
    assert limits["vcpu"]["max"] == vm_limits.ABSOLUTE["vcpu"]
    assert limits["memoire_mo"]["source"] == "politique"


def test_explicit_environment_limits_win_over_the_policy(host, monkeypatch):
    vm_limits.set_policy("libre")
    monkeypatch.setenv("HYPERLITE_VM_MAX_VCPU", "6")
    limits = vm_limits.compute_limits(force=True)
    assert limits["vcpu"]["max"] == 6
    assert limits["vcpu"]["source"] == "configuration"


def test_validation_reports_which_limit_is_exceeded(host):
    errors = vm_limits.validate_vm_resources(vcpu=64, memory_mb=999999, disk_sizes=[5000])
    assert len(errors) == 3
    assert any("vCPU" in e for e in errors)
    assert any("Memory" in e for e in errors)
    assert any("Disk 1" in e for e in errors)
    assert vm_limits.validate_vm_resources(vcpu=2, memory_mb=2048, disk_sizes=[20]) == []


def test_too_many_disks_are_rejected(host):
    assert vm_limits.validate_vm_resources(disk_sizes=[1] * 50)


def test_unknown_allocation_policy_is_rejected(database):
    with pytest.raises(ValueError):
        vm_limits.set_policy("chaos")
