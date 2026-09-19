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
from app.core import deployment_profile
from app.core.database import get_conn

MEMORY_MIN_MB = 256  # plancher fonctionnel d'une VM Linux, pas une limite d'hote
_TTL_S = 30

# Politique d'allocation : jusqu'ou un admin peut attribuer des ressources a
# UNE VM. `limites` = comportement historique (part de l'hote, garde-fou
# contre l'allocation impossible) ; `surallocation` = multiples de l'hote
# (ratios classiques de virtualisation, surtout pour vCPU et disques fins) ;
# `libre` = aucun plafond artificiel, seules les butees techniques absurdes
# restent (libvirt/QEMU refuseront eux-memes ce qu'ils ne savent pas faire,
# avec leur vraie erreur).
POLICIES = {
    "limites": {"libelle": "Limites de l'hôte",
                "description": "Une VM ne peut pas dépasser une part de la RAM, des cœurs et du disque réels de l'hôte (protège l'hôte)."},
    "surallocation": {"libelle": "Surallocation",
                      "description": "Autorise plus que le physique : jusqu'à 4x les cœurs, 1,5x la RAM et 3x l'espace disque libre (disques fins). Risque de saturation si toutes les VM sollicitent tout en même temps.",
                      "ratios": {"vcpu": 4, "memoire": 1.5, "disque": 3, "disques": 16}},
    "libre": {"libelle": "Libre",
              "description": "Aucun plafond imposé par Hyperlite : vous décidez. Seules les butées techniques absurdes restent ; libvirt/QEMU refuseront eux-mêmes l'impossible. Risque de VM qui ne démarre pas ou d'hôte saturé (OOM)."},
}
ABSOLUTE = {"vcpu": 4096, "memoire_mo": 16 * 1024 * 1024, "disque_go": 1_000_000, "disques": 64}


def env_policy():
    raw = (os.environ.get("HYPERLITE_ALLOCATION") or "").strip().lower()
    return raw if raw in POLICIES else None


def get_policy():
    """{actif, source, choix} -- env > choix admin > limites."""
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
        db.execute("INSERT INTO allocation_policy (id, politique) VALUES (1, ?) "
                   "ON CONFLICT(id) DO UPDATE SET politique = excluded.politique", (politique,))
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
        return int(usage.free / (1024 ** 3))
    except OSError:
        return None


def compute_limits(force=False):
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL_S:
        return _cache["value"]

    prof = deployment_profile.settings()  # parts de RAM/disque allouables selon le profil de deploiement (chantier 5)
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
            return {"max": fallback, "source": "repli", "detail": f"{note} -- detection impossible, valeur prudente"}
        return {"max": fallback, "source": "defaut", "detail": f"{note} (valeur par défaut, ajustable via {env_name})"}

    policy = get_policy()["actif"]
    ratios = POLICIES["surallocation"]["ratios"]
    physique = {
        "vcpu": cores, "memoire_mo": mem_total,
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
        "vcpu": {"min": 1, **pick("HYPERLITE_VM_MAX_VCPU", cores, 1, "nombre de cœurs logiques de l'hôte" + (f" x{ratios['vcpu']}" if policy == "surallocation" else ""))},
        "memoire_mo": {"min": MEMORY_MIN_MB, **pick("HYPERLITE_VM_MAX_MEMORY_MB", mem_detected, MEMORY_MIN_MB, f"{int(MEMORY_HOST_SHARE * 100)}% de la RAM de l'hôte")},
        "disque_go": {"min": 1, **pick("HYPERLITE_VM_MAX_DISK_GB", max(1, int(disk_free * DISK_FREE_SHARE)) if disk_free else None, 1, f"{int(DISK_FREE_SHARE * 100)}% de l'espace libre du pool par défaut")},
        "disques": {"min": 1, **pick("HYPERLITE_VM_MAX_DISKS", None, ratios["disques"] if policy == "surallocation" else 8, "nombre maximal de disques par VM", detected_applicable=False)},
    }
    if policy == "libre":
        for key, absolute in (("vcpu", "vcpu"), ("memoire_mo", "memoire_mo"), ("disque_go", "disque_go"), ("disques", "disques")):
            if value[key]["source"] not in ("configuration",):
                value[key].update(max=ABSOLUTE[absolute], source="politique", detail="politique d'allocation libre (butée technique seulement)")
    elif policy == "surallocation":
        for key in value:
            if value[key]["source"] == "detecte":
                value[key]["source"] = "politique"
    value["politique"] = get_policy()
    value["physique"] = physique
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
