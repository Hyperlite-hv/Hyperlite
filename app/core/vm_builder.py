import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

IMAGES_DIR = Path("/var/lib/libvirt/images")
BASE_IMAGE = IMAGES_DIR / "base" / "debian-12-generic-amd64.qcow2"
BASE_IMAGE_URL = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"

# Repertoire racine du projet (app/core/vm_builder.py -> app/core -> app -> racine),
# calcule dynamiquement pour fonctionner quel que soit le chemin d'installation.
PROJDIR = Path(__file__).resolve().parents[2]
SSH_KEY_DIR = PROJDIR / "data" / "ssh"

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

# sd(a), sd(b), sd(c)... utilise pour nommer les disques virtio-scsi d'une VM
SCSI_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def validate_name(name):
    if not NAME_RE.match(name):
        return "Nom de VM invalide (lettres/chiffres/tirets, 2-63 caracteres, doit commencer par une lettre ou un chiffre)"
    return None


def validate_username(username):
    if not USERNAME_RE.match(username):
        return "Nom d'utilisateur invalide (minuscules/chiffres/tirets/underscore, doit commencer par une lettre minuscule ou _, 32 caracteres max)"
    return None


def _ensure_automation_keypair():
    """Cle SSH dediee a Hyperlite (generee une seule fois sur le serveur), injectee
    dans le cloud-init de chaque nouvelle VM pour permettre le terminal web SSH sans
    mot de passe stocke cote serveur."""
    SSH_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = SSH_KEY_DIR / "hyperlite_automation"
    pub = SSH_KEY_DIR / "hyperlite_automation.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", "hyperlite-automation"],
            check=True, capture_output=True, text=True,
        )
        priv.chmod(0o600)
    return priv, pub


def get_or_create_automation_pubkey():
    _, pub = _ensure_automation_keypair()
    return pub.read_text().strip()


def get_automation_private_key_path():
    priv, _ = _ensure_automation_keypair()
    return priv


def ensure_base_image():
    BASE_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not BASE_IMAGE.exists():
        subprocess.run(
            ["wget", "-q", "-O", str(BASE_IMAGE), BASE_IMAGE_URL],
            check=True,
        )
    return BASE_IMAGE


def create_disk(vm_name, disk_gb, index=0, blank=False):
    """Cree un disque qcow2 pour la VM. Le disque d'index 0 (systeme) est base sur
    l'image cloud Debian par defaut ; les disques suivants sont toujours vierges
    (stockage supplementaire). `blank=True` force un disque 0 vierge malgre tout --
    utilise quand une VM demarre sur un ISO d'installation (voir create_vm) : il n'y
    a alors rien a preinstaller, l'utilisateur installe son propre OS dessus."""
    disk_path = IMAGES_DIR / (f"{vm_name}.qcow2" if index == 0 else f"{vm_name}-{index + 1}.qcow2")
    if index == 0 and not blank:
        ensure_base_image()
        subprocess.run(
            [
                "qemu-img", "create", "-f", "qcow2",
                "-F", "qcow2", "-b", str(BASE_IMAGE),
                str(disk_path), f"{disk_gb}G",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(disk_path), f"{disk_gb}G"],
            check=True,
            capture_output=True,
            text=True,
        )
    return disk_path


def create_cloudinit_iso(vm_name, username, password, ssh_pubkey=None):
    # Repertoire temporaire a permissions restreintes (0700, cree par mkdtemp),
    # toujours nettoye ensuite : user-data contient le mot de passe en clair de
    # la VM et ne doit pas survivre sur le disque hote au-dela de cette fonction.
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-"))
    try:
        user_data = workdir / "user-data"
        meta_data = workdir / "meta-data"

        if any(c in password for c in ("\n", "\r")):
            raise ValueError("Le mot de passe ne doit pas contenir de retour a la ligne")
        pwd_quoted = "'" + password.replace("'", "''") + "'"
        ud = [
            "#cloud-config",
            f"hostname: {vm_name}",
            "manage_etc_hosts: true",
            "users:",
            f"  - name: {username}",
            "    sudo: ALL=(ALL) NOPASSWD:ALL",
            "    shell: /bin/bash",
            f"    plain_text_passwd: {pwd_quoted}",
            "    lock_passwd: false",
        ]
        if ssh_pubkey:
            ud.append("    ssh_authorized_keys:")
            ud.append(f"      - {ssh_pubkey}")
        ud += [
            "chpasswd:",
            "  expire: false",
            "ssh_pwauth: true",
        ]
        user_data.write_text("\n".join(ud) + "\n")
        meta_data.write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n")

        iso_path = IMAGES_DIR / f"{vm_name}-cloudinit.iso"
        subprocess.run(
            ["cloud-localds", str(iso_path), str(user_data), str(meta_data)],
            check=True,
            capture_output=True,
            text=True,
        )
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_domain_xml(vm_name, vcpu, memory_mb, disk_paths, cloudinit_path, network="default", iso_path=None, seed_iso_path=None):
    # Ordre de boot PAR PERIPHERIQUE (<boot order='N'/> sur chaque <disk>)
    # plutot que la liste globale <os><boot dev=.../></os> : SeaBIOS ne fait
    # pas de fallback fiable entre plusieurs CD-ROM IDE avec la liste globale
    # (constate en test : il choisit le premier CD-ROM trouve, quel qu'il
    # soit, et abandonne s'il n'est pas amorcable -- ce qui echouait des que
    # l'ISO de reponses OEMDRV/cidata, jamais destine a etre amorce, passait
    # avant le vrai ISO d'installation). Avec un ordre explicite par
    # peripherique, seuls le disque systeme et l'ISO d'installation portent
    # un <boot order>, l'ISO de reponses n'en porte aucun et n'est donc
    # jamais tente comme peripherique de demarrage.
    disks_xml = ""
    for i, disk_path in enumerate(disk_paths):
        dev = f"sd{SCSI_LETTERS[i]}"
        boot_order = " <boot order='1'/>" if i == 0 else ""
        disks_xml += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='{dev}' bus='scsi'/>{boot_order}
    </disk>"""

    iso_xml = ""
    if iso_path:
        iso_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='hdd' bus='ide'/>
      <readonly/>
      <boot order='2'/>
    </disk>"""

    # cloudinit_path est optionnel : une VM demarree sur un ISO d'installation
    # (disque systeme vierge, voir create_vm) n'a pas de cloud-init a injecter,
    # l'OS et son compte utilisateur sont crees manuellement par l'installeur
    # (ou automatiquement via seed_iso_path, voir juste en dessous). Jamais de
    # <boot order> : ce disque ne doit jamais etre tente comme peripherique
    # d'amorçage, seulement lu par l'OS une fois demarre.
    cloudinit_xml = ""
    if cloudinit_path:
        cloudinit_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{cloudinit_path}'/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
    </disk>"""

    # seed_iso_path : petit ISO de reponses (OEMDRV/kickstart ou cidata/
    # autoinstall, voir app/core/unattended_install.py) que l'installeur
    # detecte tout seul une fois demarre -- aucun <boot order> ici non plus,
    # ce n'est pas un media amorcable (voir note ci-dessus).
    seed_xml = ""
    if seed_iso_path:
        seed_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed_iso_path}'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>"""

    return f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <currentMemory unit='MiB'>{memory_mb}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-model'/>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <controller type='scsi' model='virtio-scsi'/>{disks_xml}{iso_xml}{cloudinit_xml}{seed_xml}
    <interface type='network'>
      <source network='{network}'/>
      <model type='virtio'/>
    </interface>
    <console type='pty'/>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
"""
