"""VM resource bounds: no ceiling of Hyperlite's own (only technical ones) but the host's CPU threads for a VM's
vCPUs, and optional administrator caps set in the environment."""

import pytest

from app.core import vm_limits


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, database):
    for name in vm_limits.ENV_CAPS.values():
        monkeypatch.delenv(name, raising=False)
    vm_limits._cache.update(at=0.0, value=None)


@pytest.fixture()
def small_host(monkeypatch):
    """A 4-core, 16 GiB host with 100 GB of free disk."""
    monkeypatch.setattr(vm_limits.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(vm_limits, "_memory_capabilities_local", lambda: {"totale_mo": 16384})
    monkeypatch.setattr(vm_limits, "_detect_disk_free_gb", lambda: 100)


def test_the_host_size_caps_a_vms_vcpus_only(small_host):
    limits = vm_limits.compute_limits(force=True)
    assert limits["disque_go"]["source"] == "technique"
    # More memory or disk than the hardware is the administrator's choice, as in Proxmox or vSphere.
    assert vm_limits.validate_vm_resources(vcpu=4, memory_mb=65536, disk_sizes=[500]) == []
    # More vCPUs than the host's threads is refused, as on Proxmox (a cluster of 2 x 64 vCPUs on 12 threads was
    # accepted).
    assert limits["vcpu"] == {"min": 1, "max": 4, "source": "materiel", "detail": "this host has 4 CPU threads"}
    errors = vm_limits.validate_vm_resources(vcpu=5)
    assert errors and "4 CPU threads" in errors[0]
    # The hardware is still reported, for the non-blocking warning of the creation form.
    assert limits["physique"] == {"vcpu": 4, "memoire_mo": 16384, "disque_go": 100}


def test_an_administrator_cap_in_the_environment_applies_and_is_named(small_host, monkeypatch):
    monkeypatch.setenv("HYPERLITE_VM_MAX_DISK_GB", "64")
    limits = vm_limits.compute_limits(force=True)
    assert limits["disque_go"] == {
        "min": 1,
        "max": 64,
        "source": "configuration",
        "variable": "HYPERLITE_VM_MAX_DISK_GB",
    }
    errors = vm_limits.validate_vm_resources(disk_sizes=[80])
    assert errors and "HYPERLITE_VM_MAX_DISK_GB" in errors[0]


def test_floors_and_typo_ceilings_are_still_checked(small_host):
    errors = vm_limits.validate_vm_resources(vcpu=0, memory_mb=128, disk_sizes=[10_000_000])
    assert len(errors) == 3
    assert (
        any("vCPU" in e for e in errors) and any("Memory" in e for e in errors) and any("Disk 1" in e for e in errors)
    )


def test_too_many_disks_are_rejected(small_host):
    assert vm_limits.validate_vm_resources(disk_sizes=[1] * 65)
    assert vm_limits.validate_vm_resources(disk_sizes=[1] * 16) == []


def test_the_profile_and_policy_endpoints_are_gone(client, auth_headers):
    headers = auth_headers("root")
    assert client.put("/host/allocation", headers=headers, json={"politique": "libre"}).status_code in (404, 405)
    assert client.put("/host/profile", headers=headers, json={"profil": "standard"}).status_code in (404, 405)
    assert client.get("/host/limits", headers=headers).json()["disque_go"]["source"] == "technique"


def test_an_administrator_cap_lower_than_the_host_wins(small_host, monkeypatch):
    monkeypatch.setenv("HYPERLITE_VM_MAX_VCPU", "2")
    assert vm_limits.compute_limits(force=True)["vcpu"]["source"] == "configuration"
    monkeypatch.setenv("HYPERLITE_VM_MAX_VCPU", "64")
    assert vm_limits.compute_limits(force=True)["vcpu"]["max"] == 4
