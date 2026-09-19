"""Découverte de capacités hôte (mandat portabilité 2026-09-18, chantier 1
-- voir CLAUDE.md "Portabilité et robustesse infrastructure"). Construit un
profil normalisé de ce qu'un hôte (local ou distant, via le même
mécanisme SSH/qemu+ssh:// que le reste du cluster, chantier 15) peut
réellement faire -- fondation dont dépendent les chantiers suivants
(limites de VM dérivées, page "Compatibilité et capacités", diagnostic de
compatibilité de cluster).

Principe directeur du mandat : détection plutôt que supposition. Chaque
sous-fonction est best-effort et ne lève jamais -- une commande/fichier
absent renvoie `None`/`False` explicite plutôt que de faire échouer tout
le profil (dégradation contrôlée, pas d'échec global)."""
import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which

import libvirt


# --- Local uniquement (lectures /proc, /sys) --------------------------

def _cpu_capabilities_local(conn):
    caps_xml = conn.getCapabilities()
    root = ET.fromstring(caps_xml)
    host_cpu = root.find("host/cpu")
    arch = host_cpu.findtext("arch") if host_cpu is not None else platform.machine()
    model = host_cpu.findtext("model") if host_cpu is not None else None

    # Virtualisation materielle disponible : deduite de la presence d'un
    # domaine capability type='kvm' dans les capacites libvirt -- plus
    # fiable qu'un parsing manuel de /proc/cpuinfo (deja verifie par
    # libvirt lui-meme, y compris les cas ou /dev/kvm existe mais n'est
    # pas utilisable pour une raison quelconque).
    kvm_disponible = any(
        dom.get("type") == "kvm"
        for guest in root.findall("guest")
        for dom in guest.findall("arch/domain")
    )

    numa_cells = root.findall(".//topology/cells/cell")
    numa_detail = [
        {"id": cell.get("id"), "cpus": len(cell.findall(".//cpus/cpu"))}
        for cell in numa_cells
    ]

    return {
        "architecture": arch,
        "modele": model,
        "coeurs_logiques": os.cpu_count(),
        "virtualisation_materielle": kvm_disponible,
        "numa_noeuds": len(numa_detail) or None,
        "numa_detail": numa_detail or None,
    }


def _memory_capabilities_local():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    info[key] = int(rest.strip().split()[0])  # kB
    except (OSError, ValueError, IndexError):
        pass
    return {
        "totale_mo": round(info["MemTotal"] / 1024) if "MemTotal" in info else None,
        "disponible_mo": round(info["MemAvailable"] / 1024) if "MemAvailable" in info else None,
    }


def _zfs_module_loaded_local():
    """Le binaire `zfs` peut etre installe SANS que le module noyau soit
    reellement charge (ex. Secure Boot actif refusant un module DKMS
    auto-signe -- rencontre reellement sur serveur-antho le 2026-09-18,
    voir CLAUDE.md). Verifier seulement le binaire (`which zfs`) aurait
    annonce ZFS "disponible" alors que toute creation de pool y echoue
    en pratique -- exactement le genre de limitation silencieuse que le
    mandat portabilite interdit. `lsmod` est le signal direct et fiable
    (pas d'appel `zpool` qui pourrait lui-meme echouer/attendre)."""
    try:
        proc = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=3)
        return proc.returncode == 0 and any(line.split()[0] == "zfs" for line in proc.stdout.splitlines() if line.split())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _storage_capabilities_local():
    from app.core import zfs_storage
    zfs_installe = zfs_storage.is_available()
    zfs_module_charge = _zfs_module_loaded_local() if zfs_installe else False
    result = {
        "zfs_installe": zfs_installe,
        "zfs_module_charge": zfs_module_charge,
        # "disponible" = reellement utilisable maintenant, pas juste
        # installe -- c'est CE champ que le reste du code (limites
        # derivees, page Compatibilite...) doit utiliser pour decider si
        # ZFS est proposable a l'utilisateur.
        "zfs_disponible": zfs_installe and zfs_module_charge,
        "blocs": None,
    }
    try:
        proc = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            result["blocs"] = json.loads(proc.stdout).get("blockdevices")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    default_dir = Path("/var/lib/libvirt/images")
    probe_path = default_dir if default_dir.exists() else Path("/")
    try:
        import shutil as _shutil
        usage = _shutil.disk_usage(probe_path)
        result["pool_defaut_total_go"] = round(usage.total / (1024 ** 3), 1)
        result["pool_defaut_disponible_go"] = round(usage.free / (1024 ** 3), 1)
    except OSError:
        result["pool_defaut_total_go"] = None
        result["pool_defaut_disponible_go"] = None
    return result


def _network_capabilities_local(conn):
    interfaces = []
    try:
        for name in sorted(os.listdir("/sys/class/net")):
            if name == "lo":
                continue
            path = Path("/sys/class/net") / name
            try:
                etat = (path / "operstate").read_text().strip()
            except OSError:
                etat = "inconnu"
            interfaces.append({
                "nom": name,
                "pont": (path / "bridge").exists(),
                "vlan": (path / "phy80211").exists() is False and Path(f"/proc/net/vlan/{name}").exists(),
                "etat": etat,
            })
    except OSError:
        pass

    reseaux_libvirt = []
    try:
        for net in conn.listAllNetworks():
            reseaux_libvirt.append({"nom": net.name(), "actif": bool(net.isActive())})
    except libvirt.libvirtError:
        pass

    return {"interfaces": interfaces, "reseaux_libvirt": reseaux_libvirt}


def _secure_boot_state_local():
    """Lecture directe de la variable EFI plutot que `mokutil` (pas
    toujours installe, voir le blocage reel rencontre sur serveur-antho
    le 2026-09-18) -- portable a toute machine UEFI sans dependance
    externe. 4 premiers octets = attributs UEFI, dernier octet = valeur."""
    if not Path("/sys/firmware/efi").exists():
        return "bios_legacy"
    efivars = Path("/sys/firmware/efi/efivars")
    if not efivars.exists():
        return "uefi_inconnu"
    for entry in efivars.glob("SecureBoot-*"):
        try:
            data = entry.read_bytes()
            if len(data) >= 5:
                return "active" if data[-1] == 1 else "inactive"
        except OSError:
            continue
    return "uefi_inconnu"


def _software_capabilities_local():
    binaries = ["qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx"]
    result = {b: which(b) is not None for b in binaries}
    # Bibliotheque WebSocket du process Hyperlite lui-meme : sans elle,
    # shell hote / consoles VM / terminaux echouent silencieusement cote
    # navigateur (bug reel serveur-antho 2026-09-19, uvicorn installe sans
    # l'extra [standard] par requirements.txt).
    import importlib.util
    result["bibliotheque_websocket"] = any(importlib.util.find_spec(m) for m in ("websockets", "wsproto"))
    return result


def _lxc_available_local():
    try:
        lxc_conn = libvirt.open("lxc:///system")
    except libvirt.libvirtError:
        return False
    if lxc_conn is None:
        return False
    lxc_conn.close()
    return True


def _virt_capabilities_local(conn):
    return {
        "hyperviseur": conn.getType(),
        "version_libvirt": conn.getLibVersion(),
        "version_hyperviseur": conn.getVersion(),
        "conteneurs_lxc_disponibles": _lxc_available_local(),
    }


def get_local_capabilities():
    """Profil de capacites de CET hote (celui qui execute ce code)."""
    conn = libvirt.open("qemu:///system")
    if conn is None:
        raise RuntimeError("Connexion libvirt locale impossible")
    try:
        return {
            "cpu": _cpu_capabilities_local(conn),
            "memoire": _memory_capabilities_local(),
            "stockage": _storage_capabilities_local(),
            "reseau": _network_capabilities_local(conn),
            "virtualisation": _virt_capabilities_local(conn),
            "securite": {"secure_boot": _secure_boot_state_local()},
            "logiciel": _software_capabilities_local(),
        }
    finally:
        conn.close()


# --- Distant (SSH + libvirt via qemu+ssh://, meme mecanisme que le
# reste du cluster, chantier 15) ---------------------------------------

# Script shell UNIQUE (un seul aller-retour SSH, comme partout ailleurs
# dans ce projet -- ex. app/core/ha.py::_attempt_ssh_fence) plutot que
# plusieurs commandes separees : chaque ligne de sortie est prefixee par
# une etiquette stable, parsee cote Python. best-effort : une commande
# absente sur l'hote distant produit une ligne vide pour cette etiquette
# plutot que de faire echouer tout le script (grace a `|| true`/`2>/dev/null`).
_REMOTE_PROBE_SCRIPT = r"""
echo "MEMTOTAL:$(grep -m1 MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')"
echo "MEMAVAIL:$(grep -m1 MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}')"
echo "CPUCOUNT:$(nproc 2>/dev/null)"
echo "ARCH:$(uname -m 2>/dev/null)"
echo "SECUREBOOT:$([ -d /sys/firmware/efi ] && ([ -d /sys/firmware/efi/efivars ] && (for f in /sys/firmware/efi/efivars/SecureBoot-*; do [ -f "$f" ] && od -An -tu1 "$f" | tr -s ' ' | tail -c2; break; done) || echo unknown) || echo bios_legacy)"
for b in qemu-img virsh zfs zpool git gh xorriso nginx; do
  echo "BIN_${b}:$(command -v $b >/dev/null 2>&1 && echo 1 || echo 0)"
done
echo "ZFSMODULE:$(lsmod 2>/dev/null | awk '$1=="zfs"{print 1}')"
echo "DISKUSAGE:$(df -k /var/lib/libvirt/images 2>/dev/null | tail -1 | awk '{print $2, $4}')"
"""


def _parse_remote_probe(stdout):
    values = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        values[key] = val.strip()
    return values


def get_remote_capabilities(node_name):
    """Profil de capacites d'un nœud DISTANT enregistre -- combine
    l'API libvirt (fonctionne nativement via qemu+ssh://, transparent
    pour le CPU/NUMA/version/LXC) et un unique probe SSH pour les
    informations qui ne passent pas par libvirt (memoire, binaires,
    Secure Boot, usage disque -- memes limites connues que le reste du
    cluster : necessite que la confiance SSH cluster soit deja etablie,
    voir app/core/cluster.py)."""
    from app.core.cluster import get_node, get_cluster_private_key_path
    from app.core.libvirt_utils import open_conn

    node = get_node(node_name)
    if not node:
        raise ValueError(f"Nœud '{node_name}' introuvable")

    conn = open_conn(node_name)
    try:
        libvirt_part = {
            "cpu": _cpu_capabilities_local(conn),  # meme fonction : ne lit QUE via `conn`, deja transparent a distance
            "virtualisation": _virt_capabilities_local(conn),
            "reseau": {"reseaux_libvirt": [
                {"nom": n.name(), "actif": bool(n.isActive())} for n in conn.listAllNetworks()
            ], "interfaces": None},  # interfaces OS non disponibles sans SSH, voir ci-dessous
        }
    finally:
        conn.close()

    ssh_part = {"memoire": {"totale_mo": None, "disponible_mo": None}, "securite": {"secure_boot": None}, "logiciel": {}, "stockage": {"zfs_installe": None, "zfs_module_charge": None, "zfs_disponible": None, "blocs": None}}
    try:
        key_path = str(get_cluster_private_key_path())
        ssh_opts = ["-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        target = f"{node['ssh_user']}@{node['hostname']}"
        proc = subprocess.run(
            ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), target, "bash", "-s"],
            input=_REMOTE_PROBE_SCRIPT, capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            v = _parse_remote_probe(proc.stdout)
            mem_total = v.get("MEMTOTAL")
            mem_avail = v.get("MEMAVAIL")
            ssh_part["memoire"] = {
                "totale_mo": round(int(mem_total) / 1024) if mem_total and mem_total.isdigit() else None,
                "disponible_mo": round(int(mem_avail) / 1024) if mem_avail and mem_avail.isdigit() else None,
            }
            sb = v.get("SECUREBOOT", "")
            ssh_part["securite"] = {"secure_boot": (
                "active" if sb == "1" else "inactive" if sb == "0" else
                "bios_legacy" if sb == "bios_legacy" else "uefi_inconnu"
            )}
            ssh_part["logiciel"] = {
                b: v.get(f"BIN_{b}") == "1"
                for b in ("qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx")
            }
            zfs_installe = bool(ssh_part["logiciel"].get("zfs") and ssh_part["logiciel"].get("zpool"))
            zfs_module_charge = v.get("ZFSMODULE") == "1"
            ssh_part["stockage"]["zfs_installe"] = zfs_installe
            ssh_part["stockage"]["zfs_module_charge"] = zfs_module_charge
            ssh_part["stockage"]["zfs_disponible"] = zfs_installe and zfs_module_charge
            disk = v.get("DISKUSAGE", "").split()
            if len(disk) == 2 and disk[0].isdigit() and disk[1].isdigit():
                ssh_part["stockage"]["pool_defaut_total_go"] = round(int(disk[0]) / (1024 ** 2), 1)
                ssh_part["stockage"]["pool_defaut_disponible_go"] = round(int(disk[1]) / (1024 ** 2), 1)
    except (OSError, subprocess.TimeoutExpired):
        pass  # best-effort : profil partiel (libvirt seul) plutot qu'un echec total

    return {
        "cpu": libvirt_part["cpu"],
        "memoire": ssh_part["memoire"],
        "stockage": ssh_part["stockage"],
        "reseau": libvirt_part["reseau"],
        "virtualisation": libvirt_part["virtualisation"],
        "securite": ssh_part["securite"],
        "logiciel": ssh_part["logiciel"],
    }


def get_capabilities(node_name=None):
    """Point d'entree unique : profil local si node_name est None/"local",
    profil distant sinon -- meme convention que open_conn()/le reste du
    projet (chantier 15)."""
    if not node_name or node_name == "local":
        return get_local_capabilities()
    return get_remote_capabilities(node_name)
