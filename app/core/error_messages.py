"""Categorisation des erreurs techniques en causes exploitables pour les
logs/tâches (voir chantier 3 "logs et traçabilité" de la roadmap
vSphere/vCenter) : au lieu d'un "echec" brut, identifie une cause probable
(ressources insuffisantes, conflit de nom, permissions, reseau, stockage...)
tout en gardant le message d'origine pour le diagnostic technique.

Approche volontairement pragmatique par motif de texte plutot que par code
d'erreur libvirt structure : libvirt regroupe la plupart des erreurs systeme
sous un seul code generique (VIR_ERR_SYSTEM_ERROR) avec l'errno reel noye
dans le message plutot que dans un champ dedie -- filtrer sur le texte du
message est donc en pratique plus fiable (et plus portable entre versions de
libvirt) que de se fier a la taxonomie de codes.
"""
import errno

try:
    import libvirt
except ImportError:  # pas installe dans certains contextes de test
    libvirt = None

# Ordre important : le premier motif qui matche l'emporte (ex. "network" doit
# etre teste avant le fallback generique, mais apres les motifs plus
# specifiques comme "memory" au cas ou les deux apparaissent).
_PATTERNS = [
    (("cannot allocate memory", "out of memory", "failed to allocate"), "Ressources insuffisantes (RAM) sur l'hôte"),
    (("no space left on device", "not enough free space", "insufficient free space"), "Stockage insuffisant sur l'hôte"),
    (("permission denied", "access denied", "operation not permitted"), "Permissions insuffisantes côté hôte (droits fichier/libvirt)"),
    (("already exists", "already running", "already defined", "already active"), "Conflit : la ressource existe déjà ou est déjà active"),
    (("device or resource busy", "resource busy"), "Ressource occupée (déjà utilisée ailleurs sur l'hôte)"),
    (("network is unreachable", "no route to host", "network 'default' not found", "network not found"), "Problème réseau (réseau introuvable ou injoignable)"),
    (("no domain", "domain not found"), "VM introuvable côté libvirt (définition absente ou déjà supprimée)"),
]

_ERRNO_LABELS = {
    errno.ENOSPC: "Stockage insuffisant sur l'hôte",
    errno.EACCES: "Permissions insuffisantes côté hôte",
    errno.EPERM: "Permissions insuffisantes côté hôte",
    errno.ENOMEM: "Ressources insuffisantes (RAM) sur l'hôte",
    errno.EEXIST: "Conflit : le fichier/la ressource existe déjà",
    errno.EBUSY: "Ressource occupée (déjà utilisée ailleurs sur l'hôte)",
}


def describe_exception(e: Exception) -> str:
    """Renvoie '<categorie> : <message original>' quand une cause probable
    est identifiee, sinon juste str(e). Ne masque jamais le message
    d'origine -- on categorise en plus, pas a la place."""
    raw = str(e)
    lowered = raw.lower()

    errno_val = getattr(e, "errno", None)
    if errno_val in _ERRNO_LABELS:
        return f"{_ERRNO_LABELS[errno_val]} : {raw}"

    for needles, label in _PATTERNS:
        if any(n in lowered for n in needles):
            return f"{label} : {raw}"

    if libvirt is not None and isinstance(e, libvirt.libvirtError):
        try:
            code, domain = e.get_error_code(), e.get_error_domain()
            return f"Erreur libvirt (code {code}, domaine {domain}) : {raw}"
        except Exception:
            pass

    return raw
