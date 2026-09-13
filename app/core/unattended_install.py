"""Installation automatisee depuis un ISO (Kickstart pour la famille RHEL,
autoinstall/NoCloud pour Ubuntu) : construit un petit "ISO de reponses" que
l'installeur detecte tout seul au demarrage pour creer le compte utilisateur
et y installer la cle SSH d'automatisation Hyperlite, exactement comme le
fait deja le cloud-init des VM Debian.

Kickstart (RHEL et derives) n'a besoin d'aucun argument de boot : Anaconda
detecte tout seul le volume OEMDRV au demarrage. Ubuntu/autoinstall, en
revanche, a besoin du mot-cle "autoinstall" sur la ligne de commande noyau
pour sauter sa confirmation manuelle unique ("Continue with autoinstall?") --
voir extract_casper_kernel plus bas, qui extrait /casper/vmlinuz et
/casper/initrd de l'ISO pour les demarrer directement via libvirt
(<os><kernel>/<initrd>/<cmdline>), meme principe que le menu de boot construit
pour l'installeur Hyperlite Appliance (voir installer/build-iso.sh).

Limite assumee : chaque famille d'OS a son propre format de reponses ; seules
RHEL (et derives Anaconda : CentOS/Rocky/AlmaLinux/Fedora) et Ubuntu
(installeur Subiquity, ISO "live-server") sont couvertes. Un ISO non reconnu
retombe sur l'installation manuelle existante (voir vms.create_vm)."""

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from passlib.hash import sha512_crypt

from .vm_builder import IMAGES_DIR

KICKSTART_FAMILIES = ("rhel", "centos", "rocky", "almalinux", "alma-", "fedora")
AUTOINSTALL_FAMILIES = ("ubuntu",)

# Cache des noyaux/initrd casper extraits : une seule extraction par ISO
# (reutilise pour toutes les VM creees depuis le meme fichier), pas une a
# chaque creation de VM. Sous data/ comme le reste des caches/donnees propres
# a Hyperlite (data/isos, data/ssh, data/tls), pas sous le repertoire systeme
# de libvirt.
CASPER_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "casper-cache"


def detect_os_family(iso_filename):
    """Retourne 'kickstart', 'autoinstall' ou None (ISO non reconnu -> installation manuelle)."""
    name = iso_filename.lower()
    if any(k in name for k in KICKSTART_FAMILIES):
        return "kickstart"
    if any(k in name for k in AUTOINSTALL_FAMILIES):
        return "autoinstall"
    return None


def _hash_password(password):
    return sha512_crypt.hash(password)


def build_kickstart_iso(vm_name, username, password, ssh_pubkey):
    """ISO labellisee OEMDRV contenant ks.cfg : Anaconda (RHEL/CentOS/Rocky/Alma/
    Fedora) detecte automatiquement un volume OEMDRV au demarrage et l'utilise
    comme source de kickstart, sans aucun argument de boot a fournir. La cle SSH
    est ecrite via %post plutot que la directive `sshkey` (pas supportee sur
    toutes les versions d'Anaconda, %post l'est partout depuis RHEL6)."""
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-kickstart-"))
    try:
        pwd_hash = _hash_password(password)
        ks = f"""#version=RHEL9
text
reboot
cdrom
lang en_US.UTF-8
keyboard us
timezone Etc/UTC --utc
network --bootproto=dhcp --activate
rootpw --lock

user --name={username} --groups=wheel --password={pwd_hash} --iscrypted

bootloader --location=mbr
zerombr
clearpart --all --initlabel
autopart --type=lvm

%packages --ignoremissing
@core
openssh-server
%end

%post --erroronfail
mkdir -p /home/{username}/.ssh
echo "{ssh_pubkey}" >> /home/{username}/.ssh/authorized_keys
chmod 700 /home/{username}/.ssh
chmod 600 /home/{username}/.ssh/authorized_keys
chown -R {username}:{username} /home/{username}/.ssh
systemctl enable sshd
%end
"""
        ks_path = workdir / "ks.cfg"
        ks_path.write_text(ks)

        iso_path = IMAGES_DIR / f"{vm_name}-oemdrv.iso"
        subprocess.run(
            ["genisoimage", "-o", str(iso_path), "-V", "OEMDRV", "-r", "-J", str(ks_path)],
            check=True, capture_output=True, text=True,
        )
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def extract_casper_kernel(iso_path):
    """Extrait /casper/vmlinuz et /casper/initrd de l'ISO Ubuntu live-server
    pour les demarrer directement via libvirt (<os><kernel>/<initrd>), en
    ajoutant "autoinstall" sur la ligne de commande -- seul moyen de sauter
    la confirmation manuelle unique de Subiquity ("Continue with
    autoinstall?"), le mot-cle doit etre present des le demarrage du noyau,
    pas seulement dans l'ISO de reponses (voir build_autoinstall_iso).
    Ligne de commande verifiee directement dans le grub.cfg reel de l'ISO
    Ubuntu 26.04 : `linux /casper/vmlinuz  ---` + `initrd /casper/initrd`,
    aucun autre parametre requis.

    Extraction mise en cache par nom de fichier ISO (reutilisee pour toutes
    les VM creees depuis le meme fichier) plutot que refaite a chaque
    creation de VM -- l'extraction lit ~200 Mo depuis un ISO de plusieurs Go,
    pas instantane.

    Retourne (kernel_path, initrd_path)."""
    cache_dir = CASPER_CACHE_DIR / Path(iso_path).stem
    kernel_path = cache_dir / "vmlinuz"
    initrd_path = cache_dir / "initrd"
    if kernel_path.exists() and initrd_path.exists():
        return kernel_path, initrd_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    for member, dest in (("/casper/vmlinuz", kernel_path), ("/casper/initrd", initrd_path)):
        subprocess.run(
            ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", member, str(dest)],
            check=True, capture_output=True, text=True,
        )
    return kernel_path, initrd_path


def build_autoinstall_iso(vm_name, username, password, ssh_pubkey):
    """ISO NoCloud (label cidata, meme outil cloud-localds que le cloud-init
    Debian) contenant un autoinstall.yaml : Subiquity (installeur "live-server"
    d'Ubuntu) detecte cette source toute seule par etiquette de volume. Le mot-cle
    "autoinstall" doit EN PLUS etre present sur la ligne de commande noyau (voir
    extract_casper_kernel) pour sauter la confirmation manuelle unique
    ("Continue with autoinstall?") -- la seule presence de ce fichier ne
    suffit pas a elle seule."""
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-autoinstall-"))
    try:
        pwd_hash = _hash_password(password)
        user_data = f"""#cloud-config
autoinstall:
  version: 1
  locale: en_US.UTF-8
  keyboard:
    layout: us
  network:
    version: 2
    ethernets:
      any-ethernet:
        match:
          name: "en*"
        dhcp4: true
  ssh:
    install-server: true
    allow-pw: true
  identity:
    hostname: {vm_name}
    username: {username}
    password: "{pwd_hash}"
  user-data:
    disable_root: true
    users:
      - name: {username}
        lock_passwd: false
        sudo: ALL=(ALL) NOPASSWD:ALL
        ssh_authorized_keys:
          - "{ssh_pubkey}"
  storage:
    layout:
      name: direct
"""
        meta_data = f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n"

        (workdir / "user-data").write_text(user_data)
        (workdir / "meta-data").write_text(meta_data)

        iso_path = IMAGES_DIR / f"{vm_name}-autoinstall.iso"
        subprocess.run(
            ["cloud-localds", str(iso_path), str(workdir / "user-data"), str(workdir / "meta-data")],
            check=True, capture_output=True, text=True,
        )
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_seed_iso(os_family, vm_name, username, password, ssh_pubkey):
    if os_family == "kickstart":
        return build_kickstart_iso(vm_name, username, password, ssh_pubkey)
    if os_family == "autoinstall":
        return build_autoinstall_iso(vm_name, username, password, ssh_pubkey)
    raise ValueError(f"Famille d'OS non gérée : {os_family}")
