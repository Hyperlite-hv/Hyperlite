"""Per-VM resource limits DERIVED from the actual host. They replace the
hard-coded bounds (1-2 vCPU, 256-2048 MiB, 500 GB per disk) that capped every
VM at 2 vCPU / 2 GiB even on a 12-core / 16 GiB server.

Priority: environment variable (explicit administrator override) > detected
value > never an assumed fixed value. Each limit reports its `source` so the
UI can explain it."""

import os
import shutil
import time
from pathlib import Path

from app.core import deployment_profile
from app.core.database import get_conn
from app.core.host_capabilities import _memory_capabilities_local

MEMORY_MIN_MB = 256  # functional floor for a Linux VM, not a host limit
_TTL_S = 30

# Allocation policy: how far an administrator may allocate resources to ONE VM.
# `limites` = the historical behaviour (a share of the host, a guard against
# impossible allocation); `surallocation` = multiples of the host (the usual
# virtualization ratios, mostly for vCPUs and thin-provisioned disks); `libre` =
# no artificial ceiling, only absurd technical limits remain (libvirt/QEMU will
# themselves refuse what they cannot do, with their real error).
POLICIES = {
    "limites": {
        "libelle": "Host limits",
        "description": "A VM cannot exceed a share of the host's real RAM, cores and disk (protects the host).",
    },
    "surallocation": {
        "libelle": "Surallocation",
        "description": "Allows more than the hardware: up to 4x the cores, 1.5x the RAM and 3x the free disk space (thin-provisioned disks). Risk of saturation if all VMs use everything at the same time.",
        "ratios": {"vcpu": 4, "memoire": 1.5, "disque": 3, "disques": 16},
    },
    "libre": {
        "libelle": "Libre",
        "description": "No ceiling imposed by Hyperlite: you decide. Only absurd technical limits remain; libvirt/QEMU will refuse the impossible themselves. Risk of a VM that will not start or of a saturated host (OOM).",
    },
}
ABSOLUTE = {"vcpu": 4096, "memoire_mo": 16 * 1024 * 1024, "disque_go": 1_000_000, "disques": 64}


def env_policy():
    raw = (os.environ.get("HYPERLITE_ALLOCATION") or "").strip().lower()
    return raw if raw in POLICIES else None


def get_policy():
    """{actif, source, choix}: environment > admin choice > limites."""
    forced = env_policy()
    try:
        with get_conn() as db:
            row = db.execute("SELECT politique FROM allocation_policy WHERE id = 1").fetchone()
        choix = row["politique"] if row else "limites"
    except Exception:
        choix = "limites"
    if choix not in POLICIES:
        choix = "limites"
    if forced:
        return {"actif": forced, "source": "configuration", "choix": choix, "variable": "HYPERLITE_ALLOCATION"}
    return {"actif": choix, "source": "choisi" if choix != "limites" else "defaut", "choix": choix, "variable": None}


def set_policy(politique):
    if politique not in POLICIES:
        raise ValueError(f"Politique inconnue : {politique}")
    with get_conn() as db:
        db.execute(
            "INSERT INTO allocation_policy (id, politique) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET politique = excluded.politique",
            (politique,),
        )
        db.commit()
    _cache.update(at=0.0, value=None)


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

    prof = deployment_profile.settings()  # RAM/disk shares that can be allocated, depending on the deployment profile
    MEMORY_HOST_SHARE = prof["memory_host_share"]
    DISK_FREE_SHARE = prof["disk_free_share"]
    cores = os.cpu_count()
    mem_total = _memory_capabilities_local().get("totale_mo")
    disk_free = _detect_disk_free_gb()

    def pick(env_name, detected, fallback, note, detected_applicable=True):
        env_val = _env_int(env_name)
        if env_val is not None:
            return {"max": env_val, "source": "configuration", "variable": env_name}
        if detected is not None:
            return {"max": detected, "source": "detecte", "detail": note}
        if detected_applicable:
            return {
                "max": fallback,
                "source": "repli",
                "detail": f"{note}: detection failed, using a conservative value",
            }
        return {"max": fallback, "source": "defaut", "detail": f"{note} (default value, adjustable via {env_name})"}

    policy = get_policy()["actif"]
    ratios = POLICIES["surallocation"]["ratios"]
    physique = {
        "vcpu": cores,
        "memoire_mo": mem_total,
        "disque_go": int(disk_free) if disk_free is not None else None,
    }
    if policy == "surallocation":
        MEMORY_HOST_SHARE = ratios["memoire"]
        DISK_FREE_SHARE = ratios["disque"]
        if cores:
            cores = cores * ratios["vcpu"]
    mem_detected = None
    if mem_total:
        mem_detected = max(MEMORY_MIN_MB, int(mem_total * MEMORY_HOST_SHARE) // 128 * 128)

    value = {
        "vcpu": {
            "min": 1,
            **pick(
                "HYPERLITE_VM_MAX_VCPU",
                cores,
                1,
                "number of logical cores on the host" + (f" x{ratios['vcpu']}" if policy == "surallocation" else ""),
            ),
        },
        "memoire_mo": {
            "min": MEMORY_MIN_MB,
            **pick(
                "HYPERLITE_VM_MAX_MEMORY_MB",
                mem_detected,
                MEMORY_MIN_MB,
                f"{int(MEMORY_HOST_SHARE * 100)}% of the host RAM",
            ),
        },
        "disque_go": {
            "min": 1,
            **pick(
                "HYPERLITE_VM_MAX_DISK_GB",
                max(1, int(disk_free * DISK_FREE_SHARE)) if disk_free else None,
                1,
                f"{int(DISK_FREE_SHARE * 100)}% of the free space of the default pool",
            ),
        },
        "disques": {
            "min": 1,
            **pick(
                "HYPERLITE_VM_MAX_DISKS",
                None,
                ratios["disques"] if policy == "surallocation" else 8,
                "maximum number of disks per VM",
                detected_applicable=False,
            ),
        },
    }
    if policy == "libre":
        for key, absolute in (
            ("vcpu", "vcpu"),
            ("memoire_mo", "memoire_mo"),
            ("disque_go", "disque_go"),
            ("disques", "disques"),
        ):
            if value[key]["source"] not in ("configuration",):
                value[key].update(
                    max=ABSOLUTE[absolute], source="politique", detail="free allocation policy (technical ceiling only)"
                )
    elif policy == "surallocation":
        for key in value:
            if value[key]["source"] == "detecte":
                value[key]["source"] = "politique"
    value["politique"] = get_policy()
    value["physique"] = physique
    _cache.update(at=now, value=value)
    return value


def validate_vm_resources(vcpu=None, memory_mb=None, disk_sizes=None):
    """List of PRECISE error messages (empty when everything is valid): says
    which limit is reached, its value and where it comes from, never a bare
    "invalid value"."""
    limits = compute_limits()
    errors = []

    def check(label, value, lim, unit):
        if value is None:
            return
        if value < lim["min"] or value > lim["max"]:
            origine = (
                f"variable {lim['variable']}" if lim["source"] == "configuration" else lim.get("detail", lim["source"])
            )
            errors.append(f"{label}: {value}{unit} is out of limits ({lim['min']}-{lim['max']}{unit}, {origine})")

    check("vCPU", vcpu, limits["vcpu"], "")
    check("Memory", memory_mb, limits["memoire_mo"], " Mo")
    if disk_sizes is not None:
        if len(disk_sizes) > limits["disques"]["max"]:
            errors.append(f"Disks: {len(disk_sizes)} requested, maximum {limits['disques']['max']}")
        for i, size in enumerate(disk_sizes):
            check(f"Disk {i + 1}", size, limits["disque_go"], " Go")
    return errors
