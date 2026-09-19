"""Limites de ressources par VM DERIVEES de l'hote reel (mandat portabilite
2026-09-18, chantier 2 -- voir CLAUDE.md). Remplace les bornes codees en
dur (1-2 vCPU, 256-2048 Mo, 500 Go/disque) qui plafonnaient toute VM a
2 vCPU/2 Go meme sur un serveur de 12 cœurs/16 Go.

Priorite : variable d'environnement (override explicite de
l'administrateur) > valeur detectee > jamais de valeur fixe supposee.
Chaque limite indique sa `source` pour que l'UI puisse l'expliquer."""
import os
import shutil
import time
from pathlib import Path

from app.core.host_capabilities import _memory_capabilities_local

MEMORY_MIN_MB = 256  # plancher fonctionnel d'une VM Linux, pas une limite d'hote
MEMORY_HOST_SHARE = 0.8  # part de la RAM hote allouable a UNE VM (le reste : hote + autres VM)
DISK_FREE_SHARE = 0.9
_TTL_S = 30
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
        return int(usage.free / (1024 ** 3))
    except OSError:
        return None


def compute_limits(force=False):
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL_S:
        return _cache["value"]

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
            return {"max": fallback, "source": "repli", "detail": f"{note} -- detection impossible, valeur prudente"}
        return {"max": fallback, "source": "defaut", "detail": f"{note} (valeur par défaut, ajustable via {env_name})"}

    mem_detected = None
    if mem_total:
        mem_detected = max(MEMORY_MIN_MB, int(mem_total * MEMORY_HOST_SHARE) // 128 * 128)

    value = {
        "vcpu": {"min": 1, **pick("HYPERLITE_VM_MAX_VCPU", cores, 1, "nombre de cœurs logiques de l'hôte")},
        "memoire_mo": {"min": MEMORY_MIN_MB, **pick("HYPERLITE_VM_MAX_MEMORY_MB", mem_detected, MEMORY_MIN_MB, f"{int(MEMORY_HOST_SHARE * 100)}% de la RAM de l'hôte")},
        "disque_go": {"min": 1, **pick("HYPERLITE_VM_MAX_DISK_GB", max(1, int(disk_free * DISK_FREE_SHARE)) if disk_free else None, 1, f"{int(DISK_FREE_SHARE * 100)}% de l'espace libre du pool par défaut")},
        "disques": {"min": 1, **pick("HYPERLITE_VM_MAX_DISKS", None, 8, "nombre maximal de disques par VM", detected_applicable=False)},
    }
    _cache.update(at=now, value=value)
    return value


def validate_vm_resources(vcpu=None, memory_mb=None, disk_sizes=None):
    """Liste de messages d'erreur PRECIS (vide si tout est valide) -- dit
    quelle limite est atteinte, sa valeur et d'ou elle vient, jamais un
    simple "valeur invalide"."""
    limits = compute_limits()
    errors = []

    def check(label, value, lim, unit):
        if value is None:
            return
        if value < lim["min"] or value > lim["max"]:
            origine = (f"variable {lim['variable']}" if lim["source"] == "configuration"
                       else lim.get("detail", lim["source"]))
            errors.append(f"{label} : {value}{unit} hors limites ({lim['min']}-{lim['max']}{unit}, {origine})")

    check("vCPU", vcpu, limits["vcpu"], "")
    check("Mémoire", memory_mb, limits["memoire_mo"], " Mo")
    if disk_sizes is not None:
        if len(disk_sizes) > limits["disques"]["max"]:
            errors.append(f"Disques : {len(disk_sizes)} demandés, maximum {limits['disques']['max']}")
        for i, size in enumerate(disk_sizes):
            check(f"Disque {i + 1}", size, limits["disque_go"], " Go")
    return errors
