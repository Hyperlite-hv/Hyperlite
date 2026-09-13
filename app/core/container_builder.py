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
import shutil
import subprocess
from pathlib import Path

from passlib.hash import sha512_crypt

CONTAINERS_DIR = Path("/var/lib/libvirt/containers")
BASE_ROOTFS = CONTAINERS_DIR / "base" / "debian-12"
DEBOOTSTRAP_SUITE = "bookworm"
DEBOOTSTRAP_MIRROR = "http://deb.debian.org/debian"
# Variante par defaut (PAS minbase) : garantit que systemd et ses dependances
# arrivent proprement via les priorites standard de Debian plutot que d'avoir
# a toutes les lister a la main et risquer d'en oublier une -- seuls les
# paquets vraiment specifiques a Hyperlite (ssh, sudo, reseau, cle SSH) sont
# ajoutes explicitement par-dessus.
DEBOOTSTRAP_INCLUDE = "openssh-server,sudo,ifupdown,isc-dhcp-client"


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


def create_container_rootfs(name):
    """Clone rapide (copie locale, pas de reseau) de l'image de base pour un
    nouveau conteneur. `cp -a` prealable au chroot de configuration, evite de
    reimplementer une copie recursive fidele (permissions, liens
    symboliques, peripheriques speciaux /dev crees par debootstrap) a la
    main en Python."""
    ensure_base_rootfs()
    dest = container_rootfs_path(name)
    if dest.exists():
        raise ValueError(f"Le conteneur '{name}' a deja un systeme de fichiers sur disque")
    subprocess.run(["cp", "-a", str(BASE_ROOTFS), str(dest)], check=True, capture_output=True, text=True)
    return dest


def delete_container_rootfs(name):
    shutil.rmtree(container_rootfs_path(name), ignore_errors=True)


def _hash_password(password):
    return sha512_crypt.hash(password)


def configure_container_rootfs(rootfs, hostname, username, password, ssh_pubkey):
    """Personnalise un rootfs fraichement clone -- exactement l'equivalent du
    %post du kickstart RHEL ou du late_command Debian (voir
    app/core/unattended_install.py), mais applique directement sur le
    systeme de fichiers via `chroot` puisqu'il est deja accessible depuis
    l'hote, sans avoir besoin d'un premier demarrage pour executer quoi que
    ce soit."""
    pwd_hash = _hash_password(password)

    (rootfs / "etc" / "hostname").write_text(hostname + "\n")
    hosts_path = rootfs / "etc" / "hosts"
    existing_hosts = hosts_path.read_text() if hosts_path.exists() else ""
    hosts_path.write_text(f"127.0.0.1 localhost\n127.0.1.1 {hostname}\n{existing_hosts}")

    (rootfs / "etc" / "network").mkdir(parents=True, exist_ok=True)
    (rootfs / "etc" / "network" / "interfaces").write_text(
        "auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n"
    )

    subprocess.run(
        ["chroot", str(rootfs), "useradd", "-m", "-s", "/bin/bash", "-G", "sudo", username],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["chroot", str(rootfs), "chpasswd", "-e"],
        input=f"{username}:{pwd_hash}\n", check=True, capture_output=True, text=True,
    )
    # Root verrouille -- meme posture que le kickstart RHEL (rootpw --lock) :
    # seul le compte nommement cree est utilisable.
    subprocess.run(["chroot", str(rootfs), "passwd", "-l", "root"], check=True, capture_output=True, text=True)

    uid = subprocess.run(["chroot", str(rootfs), "id", "-u", username], check=True, capture_output=True, text=True).stdout.strip()
    gid = subprocess.run(["chroot", str(rootfs), "id", "-g", username], check=True, capture_output=True, text=True).stdout.strip()

    ssh_dir = rootfs / "home" / username / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(ssh_pubkey + "\n")
    authorized_keys.chmod(0o600)
    subprocess.run(["chown", "-R", f"{uid}:{gid}", str(ssh_dir)], check=True)

    # ssh.service est normalement deja active par le postinst du paquet
    # openssh-server (comportement standard de debootstrap --include) ; ce
    # symlink direct est un filet de securite explicite plutot que de
    # dependre silencieusement de ce comportement par defaut.
    wants_dir = rootfs / "etc" / "systemd" / "system" / "multi-user.target.wants"
    wants_dir.mkdir(parents=True, exist_ok=True)
    ssh_symlink = wants_dir / "ssh.service"
    if not ssh_symlink.exists():
        ssh_symlink.symlink_to("/lib/systemd/system/ssh.service")


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
