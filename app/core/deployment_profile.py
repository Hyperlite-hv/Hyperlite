"""Profils de deploiement (mandat portabilite, chantier 5 -- voir CLAUDE.md).

Trois jeux de REGLAGES PAR DEFAUT (homelab / standard / avance), pas trois
produits : le meme code tourne partout, seuls quelques parametres
d'exploitation changent. Le profil actif est, par priorite :
  1. variable d'environnement HYPERLITE_PROFILE (override de l'admin systeme)
  2. choix explicite de l'admin dans l'UI (table deployment_profile)
  3. profil RECOMMANDE, detecte depuis le materiel reel (mode "auto")
Les overrides fins existants (HYPERLITE_VM_MAX_*, chantier 2) restent
prioritaires sur tout ce qui est calcule ici."""
import os

from app.core.database import get_conn

PROFILES = {
    "homelab": {
        "libelle": "Homelab",
        "description": "Machine modeste (mini-PC, peu de RAM) : marge hôte plus large, collecte de métriques espacée pour ménager SQLite et le CPU.",
        "memory_host_share": 0.75,
        "disk_free_share": 0.85,
        "metrics_interval_s": 30,
        "vm_defaults": {"vcpu": 1, "memory_mb": 1024, "disk_gb": 10},
    },
    "standard": {
        "libelle": "Standard",
        "description": "Serveur courant : réglages historiques d'Hyperlite.",
        "memory_host_share": 0.8,
        "disk_free_share": 0.9,
        "metrics_interval_s": 15,
        "vm_defaults": {"vcpu": 2, "memory_mb": 2048, "disk_gb": 20},
    },
    "avance": {
        "libelle": "Avancé",
        "description": "Gros serveur : allocation plus agressive, métriques plus fines, VM plus généreuses par défaut.",
        "memory_host_share": 0.9,
        "disk_free_share": 0.95,
        "metrics_interval_s": 10,
        "vm_defaults": {"vcpu": 4, "memory_mb": 4096, "disk_gb": 40},
    },
}
FALLBACK = "standard"


def _read_meminfo_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def recommend_profile():
    """Profil deduit du materiel local (coeurs logiques, RAM). Detection
    impossible -> standard."""
    cores = os.cpu_count()
    mem_mb = _read_meminfo_mb()
    if cores is None and mem_mb is None:
        return FALLBACK
    if (mem_mb is not None and mem_mb >= 64 * 1024) or (cores is not None and cores >= 16):
        return "avance"
    if (mem_mb is not None and mem_mb <= 8 * 1024) or (cores is not None and cores <= 4):
        return "homelab"
    return "standard"


def _stored_choice():
    try:
        with get_conn() as db:
            row = db.execute("SELECT profil FROM deployment_profile WHERE id = 1").fetchone()
        return row["profil"] if row else "auto"
    except Exception:
        return "auto"


def set_choice(choice):
    if choice != "auto" and choice not in PROFILES:
        raise ValueError(f"Profil inconnu : {choice}")
    with get_conn() as db:
        db.execute(
            "INSERT INTO deployment_profile (id, profil) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET profil = excluded.profil", (choice,))
        db.commit()


def env_override():
    raw = (os.environ.get("HYPERLITE_PROFILE") or "").strip().lower()
    return raw if raw in PROFILES else None


def get_active():
    """{actif, source, recommande, choix, reglages}. Ne leve jamais."""
    recommande = recommend_profile()
    forced = env_override()
    choix = _stored_choice()
    if forced:
        actif, source = forced, "configuration"
    elif choix in PROFILES:
        actif, source = choix, "choisi"
    else:
        actif, source = recommande, "detecte"
    return {"actif": actif, "source": source, "recommande": recommande, "choix": choix,
            "variable": "HYPERLITE_PROFILE" if forced else None, "reglages": PROFILES[actif]}


def settings():
    return get_active()["reglages"]
