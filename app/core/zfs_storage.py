"""Stockage ZFS sur zvols bruts (backlog stockage 2026-09-18, suite du
gros chantier "se rapprocher du niveau entreprise", ZFS avant Ceph -- voir
CLAUDE.md pour la discussion complete). Choix explicite d'Antho : zvols
plutot que qcow2-sur-dataset, en vue d'une reutilisation future avec
Ceph/RBD (les deux se presentent a une VM comme un peripherique BLOC brut
sur l'hote -- seule la source du chemin de peripherique change).

Pourquoi pas l'API de pool de stockage libvirt (comme dir/netfs,
app/routers/storage.py) : verifie sur cet hote
(/usr/lib/x86_64-linux-gnu/libvirt/storage-backend/) qu'aucun pilote 'zfs'
n'est compile dans le paquet libvirt installe -- ZFS est donc gere ICI par
appels directs a `zpool`/`zfs` en sous-processus, jamais via
virStoragePool/virStorageVol. Le paquet separe
libvirt-daemon-driver-storage-zfs existe mais son support de creation de
volume est historiquement limite (liste des zvols PRE-EXISTANTS
seulement) -- pas utilise ici, tout le cycle de vie (pool ET zvol) reste
sous controle direct d'Hyperlite, sans dependance a ce driver.

Aucune table SQLite dediee : l'etat reel vit entierement dans ZFS
lui-meme (zpool list/zfs list), interroge a chaque appel -- meme
philosophie que app/routers/storage.py qui ne fait confiance qu'a l'etat
reel de libvirt, jamais a un miroir en base qui pourrait diverger.

Test initial (backlog 2026-09-18) sur pools ZFS adosses a des FICHIERS
loopback (`zpool create <nom> <fichier>`, ZFS accepte nativement un
fichier regulier comme vdev, pas besoin de `losetup` explicite) --
choix confirme par Antho pour ne pas toucher au LVM existant
(`hyperlite-vg`) ni exiger un disque dedie avant d'avoir valide le
mecanisme. Transparent pour la suite : remplacer le chemin de fichier par
un vrai peripherique bloc (`/dev/sdX`) au moment de passer sur un vrai
disque ne change rien au reste du code.
"""
import re
import subprocess
import time
from pathlib import Path

LOOPBACK_DIR = Path("/var/lib/hyperlite-zfs")

# Noms ZFS : mêmes contraintes que les noms de VM/pool ailleurs dans ce
# projet (lettres/chiffres/tirets), jamais de texte utilisateur brut
# injecté tel quel dans une commande zfs/zpool.
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}$")


class ZfsError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def validate_zfs_name(name):
    if not NAME_RE.match(name):
        return "Nom ZFS invalide (lettres/chiffres/tirets/underscore, 2-63 caractères, doit commencer par une lettre ou un chiffre)"
    return None


def _run(*args, check=True):
    # BUG REEL trouve en testant sur serveur-antho (ZFS pas installe sur
    # cette machine, contrairement a kvm-lab ou le paquet avait ete
    # installe pour developper ce chantier) : sans ce garde-fou, un
    # FileNotFoundError brut (binaire zpool/zfs absent) remontait tel
    # quel jusqu'a GET /storage -- CASSANT L'ENDPOINT ENTIER (pas
    # seulement la partie ZFS) avec une 500 sur une machine qui n'a
    # simplement pas encore ZFS. Meme classe de bug que le "git absent"
    # du chantier 7bis, pas anticipee ici malgre is_available() deja
    # ecrit -- jamais reellement branche dans ce point d'entree commun.
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True)
    except FileNotFoundError:
        if check:
            raise ZfsError("ZFS n'est pas installé sur cet hôte (binaire 'zfs'/'zpool' introuvable)")
        return subprocess.CompletedProcess(args, 127, "", "zfs/zpool introuvable")
    if check and proc.returncode != 0:
        raise ZfsError((proc.stderr or proc.stdout or f"Échec de la commande {' '.join(args)}").strip())
    return proc


def is_available():
    """True si les binaires zfs/zpool sont installés sur cet hôte -- verifié
    avant d'exposer quoi que ce soit côté API, plutôt que de laisser un
    FileNotFoundError brut remonter (même principe que le check `git`
    absent trouvé en testant le chantier 7bis)."""
    from shutil import which
    return which("zpool") is not None and which("zfs") is not None


def _backing_file_of(pool_name):
    """Chemin du fichier loopback qui sert de vdev a ce pool, retrouve en
    parsant `zpool status` -- ZFS ne stocke nulle part ailleurs cette
    info sous une forme structuree facile a interroger. Retourne None si
    non trouvable (pool sur un vrai disque, ou format de sortie inattendu
    -- jamais bloquant, juste une suppression de fichier best-effort en
    moins au moment de detruire le pool)."""
    proc = _run("zpool", "status", "-P", pool_name, check=False)
    if proc.returncode != 0:
        return None
    in_config = False
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == "config:":
            in_config = True
            continue
        if not in_config or not stripped or stripped.startswith("NAME "):
            continue
        first_token = stripped.split()[0]
        if first_token == pool_name:
            continue  # ligne du pool lui-meme, pas un vdev
        if first_token.startswith("/"):
            return first_token
        if stripped.startswith(("errors:", "mirror", "raidz")):
            continue
    return None


def list_pools():
    """Liste des pools ZFS geres par cet hote, meme forme de champs que
    _pool_summary() dans app/routers/storage.py (capacite/allocation/
    disponible en Go) pour un merge facile dans la liste unifiee de
    l'onglet Stockage."""
    proc = _run("zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health", check=False)
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        name, size, alloc, free, health = parts
        result.append({
            "nom": name,
            "type": "zfs",
            "etat": "actif" if health == "ONLINE" else health.lower(),
            "capacite_go": round(int(size) / (1024 ** 3), 2),
            "allocation_go": round(int(alloc) / (1024 ** 3), 2),
            "disponible_go": round(int(free) / (1024 ** 3), 2),
        })
    return result


def pool_exists(name):
    return _run("zpool", "list", "-H", name, check=False).returncode == 0


def create_pool(name, size_gb):
    """Cree un pool ZFS adosse a un fichier loopback de `size_gb` Go dans
    LOOPBACK_DIR. `compression=lz4` (quasi gratuit en CPU, gain reel sur
    la plupart des disques systeme) et `mountpoint=none` (ce pool ne sert
    QUE de conteneur a zvols -- jamais de dataset-fichier monte quelque
    part, aucune raison de risquer une collision de point de montage)."""
    name_error = validate_zfs_name(name)
    if name_error:
        raise ZfsError(name_error)
    if pool_exists(name):
        raise ZfsError(f"Un pool ZFS '{name}' existe déjà")

    LOOPBACK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    backing_file = LOOPBACK_DIR / f"{name}.img"
    if backing_file.exists():
        raise ZfsError(f"Le fichier de sauvegarde '{backing_file}' existe déjà (pool supprimé sans nettoyage complet ?)")

    _run("truncate", "-s", f"{size_gb}G", str(backing_file))
    try:
        _run("zpool", "create", "-O", "compression=lz4", "-O", "mountpoint=none", name, str(backing_file))
    except ZfsError:
        backing_file.unlink(missing_ok=True)
        raise
    return list_pool(name)


def list_pool(name):
    for pool in list_pools():
        if pool["nom"] == name:
            return pool
    raise ZfsError(f"Pool ZFS '{name}' introuvable")


def delete_pool(name):
    if not pool_exists(name):
        raise ZfsError(f"Pool ZFS '{name}' introuvable")
    zvols = list_zvols(name)
    if zvols:
        raise ZfsError(f"Le pool '{name}' contient encore {len(zvols)} zvol(s), supprimez-les d'abord")

    backing_file = _backing_file_of(name)
    _run("zpool", "destroy", name)
    if backing_file:
        Path(backing_file).unlink(missing_ok=True)


def list_zvols(pool_name):
    proc = _run("zfs", "list", "-t", "volume", "-H", "-p", "-o", "name,volsize,used", "-r", pool_name, check=False)
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        full_name, volsize, used = parts
        # full_name = "<pool>/<zvol>" -- on ne garde que la partie zvol,
        # le pool est deja connu de l'appelant.
        zvol_name = full_name.split("/", 1)[1] if "/" in full_name else full_name
        result.append({
            "nom": zvol_name,
            "chemin": device_path(pool_name, zvol_name),
            "capacite_go": round(int(volsize) / (1024 ** 3), 3),
            "allocation_go": round(int(used) / (1024 ** 3), 3),
        })
    return result


def device_path(pool_name, zvol_name):
    return f"/dev/zvol/{pool_name}/{zvol_name}"


def _wait_for_device(path, timeout_s=5):
    """udev met un instant a créer le nœud de périphérique après `zfs
    create -V` -- attendu et documenté (contrairement à un fichier qcow2
    classique, disponible immédiatement). Bloquant jusqu'à `timeout_s`,
    jamais plus : mieux vaut échouer clairement ensuite que renvoyer un
    chemin qui n'existe pas encore à l'appelant."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.1)
    return Path(path).exists()


def create_zvol(pool_name, zvol_name, size_gb):
    """Cree un zvol de `size_gb` Go, en fin clairsemé (thin-provisionné,
    `-s`) -- même principe que les disques qcow2 existants (pas de
    réservation immédiate de tout l'espace), attend ensuite que le nœud de
    périphérique existe réellement avant de rendre la main."""
    name_error = validate_zfs_name(zvol_name)
    if name_error:
        raise ZfsError(name_error)
    full_name = f"{pool_name}/{zvol_name}"
    if _run("zfs", "list", "-H", full_name, check=False).returncode == 0:
        raise ZfsError(f"Un zvol '{zvol_name}' existe déjà dans le pool '{pool_name}'")

    _run("zfs", "create", "-s", "-V", f"{size_gb}G", full_name)
    path = device_path(pool_name, zvol_name)
    if not _wait_for_device(path):
        raise ZfsError(f"Zvol '{full_name}' créé mais périphérique '{path}' jamais apparu (udev)")
    return path


def delete_zvol(pool_name, zvol_name):
    full_name = f"{pool_name}/{zvol_name}"
    if _run("zfs", "list", "-H", full_name, check=False).returncode != 0:
        raise ZfsError(f"Zvol '{zvol_name}' introuvable dans le pool '{pool_name}'")
    # -r : purge aussi les snapshots ZFS de ce zvol (voir zfs_snapshot ci-
    # dessous) -- une suppression de VM/disque doit être complète, jamais
    # laisser des snapshots orphelins qui empêcheraient une recréation
    # ultérieure du même nom.
    _run("zfs", "destroy", "-r", full_name)


def zvol_in_use_paths():
    """Chemins /dev/zvol/... de tous les zvols existants sur cet hôte,
    pour le même usage que get_disk_paths_in_use() dans libvirt_utils.py
    (empêcher la suppression d'un pool/zvol encore référencé) -- combiné
    côté appelant avec la liste réelle des disques de VM (XML libvirt),
    pas dupliqué ici."""
    paths = set()
    for pool in list_pools():
        for zvol in list_zvols(pool["nom"]):
            paths.add(zvol["chemin"])
    return paths
