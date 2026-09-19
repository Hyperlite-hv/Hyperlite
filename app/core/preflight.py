#!/usr/bin/env python3
"""Preflight check Hyperlite (mandat portabilite, chantier 3).

Stdlib uniquement : doit tourner sur une machine NUE, avant que le venv
existe. Chaque controle renvoie un statut parmi :
  ok         -- satisfait
  warning    -- utilisable mais degrade / a surveiller
  disabled   -- une fonctionnalite precise sera indisponible (le reste marche)
  blocking   -- l'installation ne doit pas continuer

Code de sortie : 1 s'il y a au moins un `blocking`, sinon 0.

Usage :
  python3 preflight.py [--json] [--python /root/hyperlite/venv/bin/python3]
                       [--requirements requirements.txt] [--only system|python]
                       [--offline]
"""
import argparse
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

OK, WARNING, DISABLED, BLOCKING = "ok", "warning", "disabled", "blocking"

MIN_PYTHON = (3, 11)
MIN_RAM_MB_BLOCKING = 1024
MIN_RAM_MB_WARNING = 4096
MIN_APP_DISK_GB = 2
MIN_VM_DISK_GB_WARNING = 20
APP_DIR = Path(os.environ.get("HYPERLITE_APP_DIR", "/root/hyperlite"))
VM_IMAGES_DIR = Path("/var/lib/libvirt/images")

# (module importable, distribution pip, gravite si absent, fonctionnalite)
# gravite : BLOCKING = l'application ne demarre pas / une route casse a
# l'import ; DISABLED = seule la fonctionnalite citee tombe.
PYTHON_MODULES = [
    ("fastapi", "fastapi", BLOCKING, "API web"),
    ("uvicorn", "uvicorn", BLOCKING, "serveur HTTP"),
    ("libvirt", "libvirt-python", BLOCKING, "pilotage libvirt/QEMU"),
    ("multipart", "python-multipart", BLOCKING, "envoi de fichiers (ISO, disques)"),
    ("jose", "python-jose", BLOCKING, "jetons de session (JWT)"),
    ("passlib", "passlib", BLOCKING, "hachage des mots de passe"),
    ("bcrypt", "bcrypt", BLOCKING, "hachage des mots de passe"),
    ("cryptography", "cryptography", BLOCKING, "chiffrement des secrets, SSH"),
    ("asyncssh", "asyncssh", BLOCKING, "terminaux SSH, cluster"),
    ("pyotp", "pyotp", DISABLED, "authentification 2FA (TOTP)"),
    ("qrcode", "qrcode", DISABLED, "QR code d'activation 2FA"),
]
# Une seule des deux suffit pour les WebSocket (uvicorn[standard] apporte websockets).
WEBSOCKET_MODULES = ("websockets", "wsproto")
WEBSOCKET_FEATURE = "shell hôte, consoles VM (noVNC/terminal), terminaux conteneurs"


def _c(id_, statut, message, feature=None, action=None):
    d = {"id": id_, "statut": statut, "message": message}
    if feature:
        d["fonctionnalite"] = feature
    if action:
        d["action"] = action
    return d


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _mem_total_mb():
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def _existing_parent(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _free_gb(path):
    return shutil.disk_usage(_existing_parent(path)).free / 1024 ** 3


def _secure_boot():
    if not Path("/sys/firmware/efi").exists():
        return "bios_legacy"
    for entry in Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"):
        try:
            data = entry.read_bytes()
            if len(data) >= 5:
                return "active" if data[-1] == 1 else "inactive"
        except OSError:
            continue
    return "uefi_inconnu"


def _zfs_module_loaded():
    return any(l.split()[0] == "zfs" for l in _read("/proc/modules").splitlines() if l.strip())


def _in_chroot():
    try:
        return os.stat("/").st_ino != os.stat("/proc/1/root/.").st_ino
    except OSError:
        return False


def _port_in_use(port):
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _service_active(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _apt_source_url():
    for f in list(Path("/etc/apt/sources.list.d").glob("hyperlite*")):
        for line in _read(f).splitlines():
            m = re.match(r"\s*deb\s+(?:\[[^\]]*\]\s+)?(\S+)", line)
            if m:
                return m.group(1)
    return None


def check_system(offline=False):
    out = []
    machine = platform.machine()
    if machine in ("x86_64", "AMD64"):
        out.append(_c("architecture", OK, f"Architecture {machine}"))
    else:
        out.append(_c("architecture", BLOCKING,
                      f"Architecture {machine} non supportée (le paquet est amd64 uniquement)"))

    if os.geteuid() == 0:
        out.append(_c("root", OK, "Exécuté en root"))
    else:
        out.append(_c("root", BLOCKING, "Doit être exécuté en root",
                      action="relancer avec sudo"))

    if sys.version_info >= MIN_PYTHON:
        out.append(_c("python", OK, f"Python {platform.python_version()}"))
    else:
        out.append(_c("python", BLOCKING,
                      f"Python {platform.python_version()} < {MIN_PYTHON[0]}.{MIN_PYTHON[1]} requis"))

    osr = dict(l.split("=", 1) for l in _read("/etc/os-release").splitlines() if "=" in l)
    distro = osr.get("PRETTY_NAME", "?").strip('"')
    family = (osr.get("ID", "") + " " + osr.get("ID_LIKE", "")).lower()
    if shutil.which("apt-get") and ("debian" in family or "ubuntu" in family):
        out.append(_c("distribution", OK, f"{distro} (apt disponible)"))
    else:
        out.append(_c("distribution", BLOCKING,
                      f"{distro} : famille Debian/apt requise pour le paquet hyperlite"))

    if Path("/run/systemd/system").exists():
        out.append(_c("systemd", OK, "systemd actif"))
    elif _in_chroot() and shutil.which("systemctl"):
        out.append(_c("systemd", OK, "chroot d'installation : systemd présent, démarrage du service différé au premier boot"))
    else:
        out.append(_c("systemd", BLOCKING, "systemd absent : le service hyperlite.service ne pourra pas tourner"))

    missing = [b for b in ("openssl", "ssh-keygen", "chpasswd") if not shutil.which(b)]
    if missing:
        out.append(_c("outils_installation", BLOCKING,
                      "Outils requis par l'installation absents : " + ", ".join(missing),
                      action="apt-get install openssl openssh-client passwd"))
    else:
        out.append(_c("outils_installation", OK, "openssl, ssh-keygen, chpasswd présents"))

    cpuinfo = _read("/proc/cpuinfo")
    flags = set()
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            flags = set(line.split(":", 1)[1].split())
            break
    has_virt = bool(flags & {"vmx", "svm"})
    has_kvm = Path("/dev/kvm").exists()
    feat = "machines virtuelles KVM (les conteneurs LXC restent disponibles)"
    if has_virt and has_kvm:
        out.append(_c("virtualisation_materielle", OK, "Virtualisation matérielle et /dev/kvm présents"))
    elif has_virt:
        out.append(_c("virtualisation_materielle", DISABLED,
                      "CPU compatible mais /dev/kvm absent (module kvm non chargé ou désactivé dans le BIOS)",
                      feature=feat, action="modprobe kvm_intel / kvm_amd, vérifier le BIOS"))
    elif "hypervisor" in flags:
        out.append(_c("virtualisation_materielle", DISABLED,
                      "Machine virtuelle sans virtualisation imbriquée exposée",
                      feature=feat, action="activer la virtualisation imbriquée sur l'hôte parent"))
    else:
        out.append(_c("virtualisation_materielle", DISABLED,
                      "Extensions vmx/svm absentes du CPU", feature=feat,
                      action="activer VT-x/AMD-V dans le BIOS"))

    ram = _mem_total_mb()
    if ram is None:
        out.append(_c("memoire", WARNING, "Mémoire totale non lisible"))
    elif ram < MIN_RAM_MB_BLOCKING:
        out.append(_c("memoire", BLOCKING, f"{ram} Mo de RAM : minimum {MIN_RAM_MB_BLOCKING} Mo"))
    elif ram < MIN_RAM_MB_WARNING:
        out.append(_c("memoire", WARNING,
                      f"{ram} Mo de RAM : utilisable, mais peu de marge pour les VM (recommandé {MIN_RAM_MB_WARNING} Mo)"))
    else:
        out.append(_c("memoire", OK, f"{ram} Mo de RAM"))

    app_free = _free_gb(APP_DIR)
    if app_free < MIN_APP_DISK_GB:
        out.append(_c("disque_application", BLOCKING,
                      f"{app_free:.1f} Go libres sous {APP_DIR} : minimum {MIN_APP_DISK_GB} Go"))
    else:
        out.append(_c("disque_application", OK, f"{app_free:.1f} Go libres sous {APP_DIR}"))
    vm_free = _free_gb(VM_IMAGES_DIR)
    if vm_free < MIN_VM_DISK_GB_WARNING:
        out.append(_c("disque_vm", WARNING,
                      f"{vm_free:.1f} Go libres pour les disques de VM ({VM_IMAGES_DIR}) : peu de place",
                      action="ajouter un pool de stockage sur un autre disque"))
    else:
        out.append(_c("disque_vm", OK, f"{vm_free:.1f} Go libres pour les disques de VM"))

    ifaces = []
    for name in sorted(os.listdir("/sys/class/net")) if Path("/sys/class/net").exists() else []:
        if name == "lo":
            continue
        if _read(f"/sys/class/net/{name}/operstate").strip() == "up":
            ifaces.append(name)
    if ifaces:
        out.append(_c("reseau", OK, "Interface(s) active(s) : " + ", ".join(ifaces[:6])))
    else:
        out.append(_c("reseau", WARNING, "Aucune interface réseau active : mises à jour et accès distant indisponibles"))

    if _port_in_use(8000) and not _service_active("hyperlite"):
        out.append(_c("port_8000", WARNING, "Le port 8000 est déjà utilisé par un autre processus",
                      feature="interface web Hyperlite", action="libérer le port 8000"))
    else:
        out.append(_c("port_8000", OK, "Port 8000 disponible (ou déjà servi par hyperlite)"))

    sb = _secure_boot()
    zfs_feat = "pools de stockage ZFS"
    if _zfs_module_loaded():
        out.append(_c("zfs", OK, "Module ZFS chargé"))
    elif sb == "active":
        out.append(_c("zfs", DISABLED,
                      "Secure Boot actif : un module ZFS/DKMS non signé sera refusé au chargement",
                      feature=zfs_feat, action="enrôler la clé MOK (mokutil --import) ou désactiver Secure Boot"))
    else:
        out.append(_c("zfs", DISABLED, "Module ZFS non chargé (optionnel)", feature=zfs_feat,
                      action="apt install zfsutils-linux (composant contrib)"))

    url = _apt_source_url()
    if url is None:
        out.append(_c("depot_apt", OK, "Aucun dépôt apt hyperlite configuré (installation hors ligne ou locale)"))
    elif offline:
        out.append(_c("depot_apt", OK, f"Dépôt {url} (vérification réseau sautée : --offline)"))
    else:
        try:
            with urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/dists/stable/InRelease", method="HEAD"), timeout=5):
                out.append(_c("depot_apt", OK, f"Dépôt apt joignable : {url}"))
        except Exception as e:
            out.append(_c("depot_apt", WARNING, f"Dépôt apt {url} injoignable ({type(e).__name__})",
                          feature="mises à jour Hyperlite", action="vérifier le réseau / Tailscale"))
    return out


def _parse_requirements(path):
    pins = {}
    for line in _read(path).splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s#;]+)", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


_PROBE = r"""
import importlib, importlib.metadata as md, json, sys
mods, dists, ws = json.loads(sys.argv[1])
res = {"modules": {}, "versions": {}, "websocket": None}
for m in mods:
    try:
        importlib.import_module(m); res["modules"][m] = None
    except BaseException as e:
        res["modules"][m] = type(e).__name__ + ": " + str(e)[:150]
for m in ws:
    try:
        importlib.import_module(m); res["websocket"] = m; break
    except BaseException:
        pass
for d in dists:
    try: res["versions"][d] = md.version(d)
    except Exception: res["versions"][d] = None
print(json.dumps(res))
"""


def probe_python(python=None):
    """Import REEL de chaque module dans l'interpreteur cible (pas un simple
    find_spec : un module present mais cassé -- ex. libvirt sans les .so --
    doit être détecté). python=None : interpreteur courant, en process."""
    mods = [m[0] for m in PYTHON_MODULES]
    dists = [m[1] for m in PYTHON_MODULES] + ["websockets"]
    args = json.dumps([mods, dists, list(WEBSOCKET_MODULES)])
    if python is None:
        python = sys.executable
    r = subprocess.run([python, "-c", _PROBE, args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return json.loads(r.stdout)


def check_python(python=None, requirements=None):
    if python is not None and not os.access(python, os.X_OK):
        return [_c("python_interpreteur", BLOCKING, f"Interpréteur {python} introuvable",
                   action="créer le venv puis installer requirements.txt")]
    try:
        probe = probe_python(python)
    except Exception as e:
        return [_c("python_interpreteur", BLOCKING, f"Interpréteur Python inutilisable : {e}")]
    out = []
    pins = _parse_requirements(requirements) if requirements else {}
    for mod, dist, gravite, feat in PYTHON_MODULES:
        err = probe["modules"].get(mod)
        if err is None:
            ver = probe["versions"].get(dist)
            want = pins.get(dist.lower().replace("_", "-"))
            if want and ver and ver != want:
                out.append(_c(f"python:{mod}", WARNING, f"{dist} {ver} installé, {want} attendu (requirements.txt)",
                              action="pip install -r requirements.txt"))
            else:
                out.append(_c(f"python:{mod}", OK, f"{dist} {ver or ''} importable".replace("  ", " ")))
        else:
            out.append(_c(f"python:{mod}", gravite, f"{dist} non importable : {err}",
                          feature=feat, action="pip install -r requirements.txt"))
    if probe["websocket"]:
        out.append(_c("python:websocket", OK, f"Bibliothèque WebSocket : {probe['websocket']}"))
    else:
        out.append(_c("python:websocket", DISABLED,
                      "Aucune bibliothèque WebSocket (websockets/wsproto) : installer uvicorn[standard]",
                      feature=WEBSOCKET_FEATURE, action="pip install 'uvicorn[standard]'"))
    return out


def summarize(checks):
    counts = {s: sum(1 for c in checks if c["statut"] == s) for s in (OK, WARNING, DISABLED, BLOCKING)}
    return {"bloquant": counts[BLOCKING] > 0, "compte": counts,
            "fonctionnalites_desactivees": sorted({c["fonctionnalite"] for c in checks
                                                    if c["statut"] in (DISABLED, BLOCKING) and c.get("fonctionnalite")})}


def run(python=None, requirements=None, only=None, offline=False):
    checks = []
    if only in (None, "system"):
        checks += check_system(offline=offline)
    if only in (None, "python"):
        checks += check_python(python, requirements)
    return {"resume": summarize(checks), "controles": checks}


_ICON = {OK: "[ OK ]", WARNING: "[WARN]", DISABLED: "[OFF ]", BLOCKING: "[FAIL]"}


def format_report(report):
    lines = ["Hyperlite -- preflight check", ""]
    for c in report["controles"]:
        lines.append(f"{_ICON[c['statut']]} {c['message']}")
        if c["statut"] != OK:
            if c.get("fonctionnalite"):
                lines.append(f"       impact : {c['fonctionnalite']}")
            if c.get("action"):
                lines.append(f"       action : {c['action']}")
    r = report["resume"]["compte"]
    lines += ["", f"Résumé : {r[OK]} ok, {r[WARNING]} avertissement(s), {r[DISABLED]} fonctionnalité(s) désactivée(s), {r[BLOCKING]} bloquant(s)"]
    if report["resume"]["bloquant"]:
        lines.append("=> Installation NON recommandée : corrigez les points [FAIL] ci-dessus.")
    elif r[DISABLED] or r[WARNING]:
        lines.append("=> Installation possible en mode dégradé (voir [OFF]/[WARN]).")
    else:
        lines.append("=> Tout est en ordre.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--python", help="interpréteur à sonder (ex. le venv)")
    ap.add_argument("--requirements", help="requirements.txt pour comparer les versions")
    ap.add_argument("--only", choices=["system", "python"])
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    report = run(a.python, a.requirements, a.only, a.offline)
    print(json.dumps(report, ensure_ascii=False, indent=2) if a.json else format_report(report))
    return 1 if report["resume"]["bloquant"] else 0


if __name__ == "__main__":
    sys.exit(main())
