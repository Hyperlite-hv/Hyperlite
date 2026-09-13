"""Conteneurs (chantier 18) : construction et configuration des conteneurs
LXC via le pilote LXC natif de libvirt (lxc:///system, distinct de
qemu:///system utilise pour les VM -- voir app/core/libvirt_utils.py).

Principe, en miroir de app/core/vm_builder.py pour les VM :
- Une seule image de base construite via debootstrap (Debian 12), mise en
  cache sous CONTAINERS_DIR/base/ -- rebootstraper a chaque creation de
  conteneur prendrait plusieurs minutes et necessiterait une connexion
  internet a chaque fois, exactement le meme raisonnement que
  vm_builder.ensure_base_image() pour l'image cloud Debian des VM.
- Chaque nouveau conteneur clone cette base par une copie locale rapide
  (cp -a), puis la personnalise (hostname, compte utilisateur, mot de passe,
  cle SSH d'automatisation) directement sur les fichiers via `chroot` --
  pas besoin de cloud-init ni de kickstart/preseed ici, le systeme de
  fichiers du conteneur est directement accessible depuis l'hote avant meme
  son premier demarrage.
- Le conteneur demarre avec /sbin/init (systemd) comme PID 1 : un vrai
  systeme Debian minimal, pas juste un shell, pour beneficier de la gestion
  de services standard (sshd, réseau via ifupdown+dhclient) exactement comme
  un systeme installe classique.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from passlib.hash import sha512_crypt

CONTAINERS_DIR = Path("/var/lib/libvirt/containers")
BASE_ROOTFS = CONTAINERS_DIR / "base" / "debian-12"
# Images tirees d'un registre (Docker Hub par defaut, ou tout registre OCI --
# "ghcr.io/foo/bar:tag" fonctionne aussi) : mises en cache separement de la
# base locale ci-dessus, une par reference d'image demandee.
PULLED_IMAGES_DIR = CONTAINERS_DIR / "base" / "images"
DEBOOTSTRAP_SUITE = "bookworm"
DEBOOTSTRAP_MIRROR = "http://deb.debian.org/debian"
# Variante par defaut (PAS minbase) : garantit que systemd et ses dependances
# arrivent proprement via les priorites standard de Debian plutot que d'avoir
# a toutes les lister a la main et risquer d'en oublier une -- seuls les
# paquets vraiment specifiques a Hyperlite (ssh, sudo) sont ajoutes
# explicitement par-dessus. PAS de ifupdown/isc-dhcp-client : le profil
# AppArmor de libvirtd sur cet hote (Debian standard) interdit d'envoyer un
# signal a dhclient (`apparmor="DENIED" ... signal=term ... peer="/sbin/
# dhclient"`, constate en test) -- detruire un conteneur dont l'interface a
# ete configuree par dhclient echoue silencieusement cote noyau, le
# processus reste orphelin. systemd-networkd (deja fourni par le paquet
# systemd, voir configure_container_rootfs) a son propre client DHCP
# integre, sans binaire externe a confiner separement -- aucun conflit.
DEBOOTSTRAP_INCLUDE = "openssh-server,sudo"


def ensure_base_rootfs():
    """Construit (une seule fois) l'image de base des conteneurs. Operation
    lente (plusieurs minutes, telechargement reseau) : appelee explicitement
    avant la premiere creation de conteneur, pas a chaque fois -- voir
    create_container_rootfs."""
    if (BASE_ROOTFS / "bin" / "sh").exists():
        return BASE_ROOTFS
    BASE_ROOTFS.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(BASE_ROOTFS, ignore_errors=True)
    subprocess.run(
        [
            "debootstrap", "--arch=amd64", f"--include={DEBOOTSTRAP_INCLUDE}",
            DEBOOTSTRAP_SUITE, str(BASE_ROOTFS), DEBOOTSTRAP_MIRROR,
        ],
        check=True, capture_output=True, text=True,
    )
    return BASE_ROOTFS


def container_rootfs_path(name):
    return CONTAINERS_DIR / name


# ---- Images tirees d'un registre (Docker Hub ou autre, chantier 18) ----
#
# skopeo (recupere l'image, sans demon Docker) + umoci (deballe les couches
# OCI en un systeme de fichiers exploitable) : verifie en pratique sur ce
# host que l'image officielle "debian:12" du Hub, une fois deballee, N'A
# PAS systemd (pas de /sbin/init -- les images Docker sont concues pour un
# seul processus, pas un OS complet) mais A un vrai gestionnaire de paquets
# (apt-get) -- suffisant pour y installer systemd/ssh/sudo apres coup,
# exactement comme pour la base locale debootstrap. Les images VRAIMENT
# minimales (scratch, distroless, sans gestionnaire de paquets) ne sont pas
# prises en charge : bootstrap_os_container leve une erreur explicite
# plutot que de produire un conteneur inutilisable en silence.
IMAGE_REF_SAFE_RE = re.compile(r"[^a-zA-Z0-9]+")


def _image_cache_dir(image_ref):
    return PULLED_IMAGES_DIR / IMAGE_REF_SAFE_RE.sub("_", image_ref)


def pull_image_rootfs(image_ref):
    """Tire une image depuis un registre OCI/Docker (Docker Hub par defaut
    si aucun registre n'est precise dans la reference, comme `docker pull`)
    et la deballe en systeme de fichiers, mis en cache par reference exacte
    (un `nginx:latest` re-demande plus tard reutilise le cache -- les tags
    mobiles comme `latest` ne sont donc PAS re-verifies a chaque creation,
    meme compromis que la base locale debootstrap)."""
    cache_dir = _image_cache_dir(image_ref)
    rootfs = cache_dir / "rootfs"
    if (rootfs / "bin").exists() or (rootfs / "usr" / "bin").exists():
        return rootfs

    PULLED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(cache_dir, ignore_errors=True)
    source = image_ref if "://" in image_ref else f"docker://docker.io/{image_ref}"
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-image-pull-"))
    try:
        oci_dir = workdir / "oci"
        subprocess.run(
            ["skopeo", "copy", source, f"oci:{oci_dir}:latest"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["umoci", "unpack", "--image", f"{oci_dir}:latest", str(cache_dir)],
            check=True, capture_output=True, text=True,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not rootfs.exists():
        raise ValueError(f"Image '{image_ref}' recuperee mais deballage invalide (pas de rootfs/)")
    _ensure_dev_nodes(rootfs)
    return rootfs


def _ensure_dev_nodes(rootfs):
    """Cree les noeuds de peripherique /dev essentiels dans un rootfs tire
    d'un registre -- une image Docker ne les contient PAS (le runtime Docker
    les fournit lui-meme au demarrage du conteneur, pas l'image), constate
    en test reel : /dev/null y est un simple FICHIER VIDE de 0 octet, pas un
    vrai device (`c 1 3`) -- gpg/apt-key et bien d'autres outils echouent des
    qu'ils essaient d'y ecrire ("cannot create /dev/null: Permission
    denied"). debootstrap (base locale) les cree deja, ce correctif ne
    concerne donc que les images tirees d'un registre."""
    dev = rootfs / "dev"
    dev.mkdir(exist_ok=True)
    nodes = [
        ("null", "c", 1, 3, 0o666), ("zero", "c", 1, 5, 0o666), ("full", "c", 1, 7, 0o666),
        ("random", "c", 1, 8, 0o666), ("urandom", "c", 1, 9, 0o666),
        ("tty", "c", 5, 0, 0o666), ("console", "c", 5, 1, 0o600), ("ptmx", "c", 5, 2, 0o666),
    ]
    for devname, kind, major, minor, mode in nodes:
        path = dev / devname
        if path.is_symlink() or path.exists():
            if path.is_file() and not path.is_symlink():
                path.unlink()
            else:
                continue
        subprocess.run(["mknod", "-m", oct(mode)[2:], str(path), kind, str(major), str(minor)], check=True, capture_output=True, text=True)


def detect_package_family(rootfs):
    """Devine le gestionnaire de paquets d'un rootfs tire d'une image
    externe (contrairement a la base locale, dont on connait deja la
    famille) -- Debian/Ubuntu (apt) et Alpine (apk) couvrent l'immense
    majorite des images publiques reelles (la plupart des images
    "minimales" comme nginx/redis/postgres sont encore basees sur l'un des
    deux sous le capot, pas des images "scratch" totalement nues)."""
    if (rootfs / "usr" / "bin" / "apt-get").exists() or (rootfs / "usr" / "bin" / "dpkg").exists():
        return "apt"
    if (rootfs / "sbin" / "apk").exists() or (rootfs / "usr" / "sbin" / "apk").exists() or (rootfs / "usr" / "bin" / "apk").exists():
        return "apk"
    return None


def bootstrap_os_container(rootfs):
    """Installe systemd (ou l'init natif d'Alpine, deja fourni par
    alpine-base) + openssh-server + sudo dans un rootfs tire d'un registre --
    les images publiques n'ont normalement ni l'un ni l'autre (concues pour
    faire tourner UN processus, pas un OS complet avec acces SSH comme les
    conteneurs Hyperlite). Reseau temporaire (resolv.conf de l'hote copie le
    temps de l'installation, l'image tiree n'en a pas forcement un valide) --
    remplace ensuite par le resolv.conf statique final dans
    configure_container_rootfs."""
    family = detect_package_family(rootfs)
    if family is None:
        raise ValueError(
            "Cette image n'a pas de gestionnaire de paquets reconnu (apt/apk) -- "
            "probablement une image minimale a processus unique (scratch/distroless), "
            "pas compatible avec le modele conteneur Hyperlite (systeme complet + acces SSH)."
        )

    host_resolv = Path("/etc/resolv.conf")
    target_resolv = rootfs / "etc" / "resolv.conf"
    original_resolv = target_resolv.read_bytes() if target_resolv.exists() else None
    if host_resolv.exists():
        target_resolv.parent.mkdir(parents=True, exist_ok=True)
        target_resolv.write_bytes(host_resolv.read_bytes())

    try:
        if family == "apt":
            env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            # Poule et oeuf constate en test reel : "apt-get update" echoue
            # d'emblee avec "gpgv...required for verification" -- les images
            # Docker officielles minimales n'ont pas gnupg preinstalle, or
            # c'est justement gnupg qu'il faudrait installer pour verifier
            # les depots et pouvoir installer quoi que ce soit d'autre.
            # Verification desactivee UNIQUEMENT le temps d'installer gnupg
            # lui-meme (depots Debian officiels, deja references tels quels
            # dans l'image -- pas une source ajoutee par Hyperlite), jamais
            # pour les paquets installes ensuite.
            insecure = ["-o", "Acquire::AllowInsecureRepositories=true", "-o", "APT::Get::AllowUnauthenticated=true"]
            subprocess.run(["chroot", str(rootfs), "apt-get", *insecure, "update"], check=True, capture_output=True, text=True, env=env)
            subprocess.run(
                ["chroot", str(rootfs), "apt-get", "install", "-y", *insecure, "--no-install-recommends", "gnupg"],
                check=True, capture_output=True, text=True, env=env,
            )
            subprocess.run(["chroot", str(rootfs), "apt-get", "update"], check=True, capture_output=True, text=True, env=env)
            subprocess.run(
                ["chroot", str(rootfs), "apt-get", "install", "-y", "--no-install-recommends", "systemd", "systemd-sysv", "openssh-server", "sudo"],
                check=True, capture_output=True, text=True, env=env,
            )
        elif family == "apk":
            subprocess.run(["chroot", str(rootfs), "apk", "update"], check=True, capture_output=True, text=True)
            # openrc EXPLICITE : contrairement a l'hypothese initiale,
            # l'image officielle "alpine" du Docker Hub n'a PAS openrc
            # preinstalle (verifie en test reel : /etc/init.d/ vide apres
            # config, "sshd"/"networking" restaient des symlinks casses
            # pointant vers des scripts inexistants, le conteneur demarrait
            # bien jusqu'a getty mais sans reseau ni sshd) -- contrairement a
            # l'appliance Alpine complete utilisee par le kickstart des VM
            # (unattended_install.py), une image Docker minimale n'a que
            # busybox + apk. + shadow/bash : BusyBox fournit "adduser" (pas
            # "useradd") et pas de bash par defaut -- installes pour que
            # configure_container_rootfs (useradd/chpasswd -e/bash) marche
            # a l'identique quelle que soit la famille, sans avoir a la
            # dupliquer par famille de paquets.
            subprocess.run(
                ["chroot", str(rootfs), "apk", "add", "--no-cache", "openrc", "openssh", "sudo", "shadow", "bash"],
                check=True, capture_output=True, text=True,
            )
    finally:
        # Rendu au resolv.conf d'origine de l'image (ou supprime s'il
        # n'existait pas) : configure_container_rootfs pose le sien juste
        # apres de toute facon, mais autant ne pas laisser une fuite du
        # resolv.conf de l'HOTE dans le rootfs final par accident.
        if original_resolv is not None:
            target_resolv.write_bytes(original_resolv)
        else:
            target_resolv.unlink(missing_ok=True)

    return family


def create_container_rootfs(name, image=None):
    """Clone rapide (copie locale, pas de reseau) de l'image de base --
    locale (debootstrap, par defaut) ou tiree d'un registre externe
    (`image`, ex. "ubuntu:22.04", "alpine:3.19", "ghcr.io/foo/bar:tag") --
    pour un nouveau conteneur. `cp -a` prealable au chroot de configuration,
    evite de reimplementer une copie recursive fidele (permissions, liens
    symboliques, peripheriques speciaux /dev) a la main en Python.

    Retourne (chemin_rootfs, famille) -- famille ("apt" ou "apk") a passer
    telle quelle a configure_container_rootfs, qui en a besoin pour la
    configuration reseau/service (systemd vs OpenRC)."""
    if image:
        base = pull_image_rootfs(image)
        family = detect_package_family(base)
        # Bootstrap systemd/ssh/sudo UNE FOIS sur l'image mise en cache, pas
        # a chaque conteneur clone depuis elle -- meme raisonnement que
        # debootstrap pour la base locale.
        marker = base.parent / ".hyperlite-bootstrapped"
        if not marker.exists():
            bootstrap_os_container(base)
            marker.touch()
    else:
        base = ensure_base_rootfs()
        family = "apt"

    dest = container_rootfs_path(name)
    if dest.exists():
        raise ValueError(f"Le conteneur '{name}' a deja un systeme de fichiers sur disque")
    subprocess.run(["cp", "-a", str(base), str(dest)], check=True, capture_output=True, text=True)
    return dest, family


def delete_container_rootfs(name):
    shutil.rmtree(container_rootfs_path(name), ignore_errors=True)


def _hash_password(password):
    return sha512_crypt.hash(password)


def configure_container_rootfs(rootfs, hostname, username, password, ssh_pubkey, family="apt"):
    """Personnalise un rootfs fraichement clone -- exactement l'equivalent du
    %post du kickstart RHEL ou du late_command Debian (voir
    app/core/unattended_install.py), mais applique directement sur le
    systeme de fichiers via `chroot` puisqu'il est deja accessible depuis
    l'hote, sans avoir besoin d'un premier demarrage pour executer quoi que
    ce soit. `family` ("apt" ou "apk", voir detect_package_family) ne change
    que la configuration reseau/service (systemd vs OpenRC) -- compte
    utilisateur/mot de passe/sudo/cle SSH sont identiques dans les deux cas
    (shadow/bash/sudo installes sur les deux familles, voir
    bootstrap_os_container)."""
    pwd_hash = _hash_password(password)

    (rootfs / "etc" / "hostname").write_text(hostname + "\n")
    hosts_path = rootfs / "etc" / "hosts"
    existing_hosts = hosts_path.read_text() if hosts_path.exists() else ""
    hosts_path.write_text(f"127.0.0.1 localhost\n127.0.1.1 {hostname}\n{existing_hosts}")

    if family == "apk":
        # Alpine (OpenRC, pas systemd -- voir bootstrap_os_container) :
        # DHCP via busybox udhcpc/ifupdown classique, pas de dhclient separe
        # (le binaire specifiquement concerne par le blocage AppArmor
        # constate sur cet hote pour les VM Kali, voir unattended_install.py
        # -- busybox udhcpc est un binaire/chemin different, pas soumis au
        # meme profil AppArmor Debian, mais pas verifie explicitement ici).
        net_dir = rootfs / "etc" / "network"
        net_dir.mkdir(parents=True, exist_ok=True)
        (net_dir / "interfaces").write_text("auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n")
        runlevels_dir = rootfs / "etc" / "runlevels" / "default"
        runlevels_dir.mkdir(parents=True, exist_ok=True)
        for service, init_script in (("networking", "/etc/init.d/networking"), ("sshd", "/etc/init.d/sshd")):
            symlink = runlevels_dir / service
            if not symlink.exists():
                symlink.symlink_to(init_script)
    else:
        # systemd-networkd (pas ifupdown/dhclient, voir DEBOOTSTRAP_INCLUDE
        # plus haut) : DHCP integre, active explicitement (pas actif par
        # defaut sur Debian) via le meme mecanisme de symlink que
        # ssh.service plus bas.
        network_dir = rootfs / "etc" / "systemd" / "network"
        network_dir.mkdir(parents=True, exist_ok=True)
        (network_dir / "eth0.network").write_text("[Match]\nName=eth0\n\n[Network]\nDHCP=yes\n")

    # /etc/resolv.conf statique plutot que le stub de systemd-resolved (non
    # active ici, inutile d'ajouter un service de plus pour ce premier jet) --
    # resolveur public, suffisant pour un conteneur qui a besoin du reseau
    # sortant (ex. `apt install` manuel une fois connecte).
    (rootfs / "etc" / "resolv.conf").write_text("nameserver 1.1.1.1\nnameserver 9.9.9.9\n")

    # Pas de -G sudo : le groupe "sudo" n'existe pas forcement (Alpine ne le
    # cree pas) et n'est de toute facon pas necessaire -- l'acces sudo est
    # accorde nommement a cet utilisateur via sudoers.d plus bas, pas par
    # appartenance a un groupe.
    subprocess.run(
        ["chroot", str(rootfs), "useradd", "-m", "-s", "/bin/bash", username],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["chroot", str(rootfs), "chpasswd", "-e"],
        input=f"{username}:{pwd_hash}\n", check=True, capture_output=True, text=True,
    )
    # Root verrouille -- meme posture que le kickstart RHEL (rootpw --lock) :
    # seul le compte nommement cree est utilisable.
    subprocess.run(["chroot", str(rootfs), "passwd", "-l", "root"], check=True, capture_output=True, text=True)

    # sudo SANS mot de passe pour ce compte -- l'appartenance seule au
    # groupe "sudo" ne suffit pas (politique par defaut Debian : mot de
    # passe requis), constate en test (le terminal web pouvait se connecter
    # en SSH mais `sudo` y restait bloque). Meme posture que l'autoinstall
    # Ubuntu des VM (`sudo: ALL=(ALL) NOPASSWD:ALL`, voir
    # unattended_install.py::build_autoinstall_iso).
    sudoers_dropin = rootfs / "etc" / "sudoers.d" / "hyperlite-automation"
    sudoers_dropin.write_text(f"{username} ALL=(ALL) NOPASSWD:ALL\n")
    sudoers_dropin.chmod(0o440)

    uid = subprocess.run(["chroot", str(rootfs), "id", "-u", username], check=True, capture_output=True, text=True).stdout.strip()
    gid = subprocess.run(["chroot", str(rootfs), "id", "-g", username], check=True, capture_output=True, text=True).stdout.strip()

    ssh_dir = rootfs / "home" / username / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(ssh_pubkey + "\n")
    authorized_keys.chmod(0o600)
    subprocess.run(["chown", "-R", f"{uid}:{gid}", str(ssh_dir)], check=True)

    if family != "apk":
        # ssh.service est normalement deja active par le postinst du paquet
        # openssh-server (comportement standard de debootstrap --include) ;
        # ce symlink direct est un filet de securite explicite plutot que de
        # dependre silencieusement de ce comportement par defaut. systemd-
        # networkd, lui, n'est PAS active par defaut sur Debian (a la
        # difference de ssh une fois le paquet installe) -- symlink
        # obligatoire ici, pas juste un filet de securite. (Alpine/apk :
        # deja gere plus haut via les runlevels OpenRC.)
        wants_dir = rootfs / "etc" / "systemd" / "system" / "multi-user.target.wants"
        wants_dir.mkdir(parents=True, exist_ok=True)
        for service, unit_path in (
            ("ssh.service", "/lib/systemd/system/ssh.service"),
            ("systemd-networkd.service", "/lib/systemd/system/systemd-networkd.service"),
        ):
            symlink = wants_dir / service
            if not symlink.exists():
                symlink.symlink_to(unit_path)


def build_container_xml(name, vcpu, memory_mb, rootfs, network="default", mac=None):
    """Domaine LXC minimal : /sbin/init (systemd) comme PID 1, systeme de
    fichiers monte directement depuis le rootfs sur disque (pas de disque
    virtuel/qcow2 comme pour les VM -- les conteneurs partagent le noyau de
    l'hote, il n'y a pas de disque a emuler). Les limites CPU/RAM sont
    imposees nativement par cgroups (<vcpu>/<memory>), pas besoin du
    XML <cputune>/<memtune> utilise pour les VM QEMU (chantier 6)."""
    mac_xml = f"\n      <mac address='{mac}'/>" if mac else ""
    return f"""
<domain type='lxc'>
  <name>{name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <currentMemory unit='MiB'>{memory_mb}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
  <os>
    <type arch='x86_64'>exe</type>
    <init>/sbin/init</init>
  </os>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <console type='pty'/>
    <filesystem type='mount' accessmode='passthrough'>
      <source dir='{rootfs}'/>
      <target dir='/'/>
    </filesystem>
    <interface type='network'>
      <source network='{network}'/>{mac_xml}
    </interface>
  </devices>
</domain>
"""
