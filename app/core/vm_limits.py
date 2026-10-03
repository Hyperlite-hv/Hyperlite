"""Per-VM resource bounds.

Like Proxmox and vSphere, Hyperlite imposes no ceiling of its own on a VM's memory or disks: the administrator
decides, and libvirt/QEMU refuse what they cannot do with their real error. What remains:
  - technical floors (1 vCPU, 256 MiB, 1 GB) and absurd-value ceilings that stop a typo, not a choice;
  - a VM's vCPUs: at most this host's CPU threads, as on Proxmox (overcommitment across VMs stays allowed);
  - optional caps an administrator sets on purpose in the environment (HYPERLITE_VM_MAX_*), for example on a
    small test machine.
Each bound reports its `source` so the UI can explain it. The host's physical resources are returned too, only
for a non-blocking "more than the hardware" warning in the creation form."""

import os
import shutil
import time
from pathlib import Path

from app.core.host_capabilities import _memory_capabilities_local

MEMORY_MIN_MB = 256  # functional floor for a Linux VM, not a host limit
_TTL_S = 30

# Beyond these values the request is a typo, not a sizing decision.
TECHNICAL_MAX = {"vcpu": 4096, "memoire_mo": 16 * 1024 * 1024, "disque_go": 1_000_000, "disques": 64}
ENV_CAPS = {
    "vcpu": "HYPERLITE_VM_MAX_VCPU",
    "memoire_mo": "HYPERLITE_VM_MAX_MEMORY_MB",
    "disque_go": "HYPERLITE_VM_MAX_DISK_GB",
    "disques": "HYPERLITE_VM_MAX_DISKS",
}
MINIMUMS = {"vcpu": 1, "memoire_mo": MEMORY_MIN_MB, "disque_go": 1, "disques": 1}

_cache = {"at": 0.0, "value": None}


def _env_int(name):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _detect_disk_free_gb():
    default_dir = Path("/var/lib/libvirt/images")
    try:
        usage = shutil.disk_usage(default_dir if default_dir.exists() else Path("/"))
        return int(usage.free / (1024**3))
    except OSError:
        return None


def compute_limits(force=False):
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL_S:
        return _cache["value"]

    value = {}
    for key, variable in ENV_CAPS.items():
        cap = _env_int(variable)
        if cap is not None:
            value[key] = {"min": MINIMUMS[key], "max": cap, "source": "configuration", "variable": variable}
        else:
            value[key] = {
                "min": MINIMUMS[key],
                "max": TECHNICAL_MAX[key],
                "source": "technique",
                "detail": "no limit set by Hyperlite (technical ceiling only)",
            }
    # A VM never gets more vCPUs than this host has CPU threads, as on Proxmox: they would only queue for the same
    # threads (and a guest spinning on a lock held by a descheduled vCPU stalls). The vCPUs of all the VMs together
    # may still exceed it: that is overcommitment, a sizing choice. A cluster of 2 x 64 vCPUs was accepted on a host
    # of 12 threads until the audit before 1.0.0.
    threads = os.cpu_count()
    if threads and value["vcpu"]["max"] > threads:
        value["vcpu"] = {
            "min": MINIMUMS["vcpu"],
            "max": threads,
            "source": "materiel",
            "detail": f"this host has {threads} CPU threads",
        }
    disk_free = _detect_disk_free_gb()
    value["physique"] = {
        "vcpu": os.cpu_count(),
        "memoire_mo": _memory_capabilities_local().get("totale_mo"),
        "disque_go": int(disk_free) if disk_free is not None else None,
    }
    _cache.update(at=now, value=value)
    return value


def validate_vm_resources(vcpu=None, memory_mb=None, disk_sizes=None):
    """List of PRECISE error messages (empty when everything is valid): says
    which bound is reached, its value and where it comes from, never a bare
    "invalid value"."""
    limits = compute_limits()
    errors = []

    def check(label, value, lim, unit):
        if value is None:
            return
        if value < lim["min"] or value > lim["max"]:
            origine = f"variable {lim['variable']}" if lim["source"] == "configuration" else lim["detail"]
            errors.append(f"{label}: {value}{unit} is out of limits ({lim['min']}-{lim['max']}{unit}, {origine})")

    check("vCPU", vcpu, limits["vcpu"], "")
    check("Memory", memory_mb, limits["memoire_mo"], " MB")
    if disk_sizes is not None:
        if len(disk_sizes) > limits["disques"]["max"]:
            errors.append(f"Disks: {len(disk_sizes)} requested, maximum {limits['disques']['max']}")
        for i, size in enumerate(disk_sizes):
            check(f"Disk {i + 1}", size, limits["disque_go"], " GB")
    return errors
