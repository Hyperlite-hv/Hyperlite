import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

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
        return "Nom de VM invalide (lettres/chiffres/tirets, 2-63 caractères, doit commencer par une lettre ou un chiffre)"
    return None


def validate_username(username):
    if not USERNAME_RE.match(username):
        return "Nom d'utilisateur invalide (minuscules/chiffres/tirets/underscore, doit commencer par une lettre minuscule ou _, 32 caractères max)"
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


def create_disk_from_import(vm_name, source_path):
    """Cree le disque systeme (index 0) d'une VM a partir d'un fichier
    disque deja uploade (voir app/routers/vm_disks.py, chantier 23) plutot
    que de l'image cloud Debian par defaut ou d'un disque vierge -- chemin
    "importer une VM depuis un disque" du formulaire de creation, alternatif
    a ISO+kickstart. `qemu-img convert` detecte tout seul le format source
    (raw/vmdk/vdi/vhd/qcow2/...), rien a lui preciser."""
    disk_path = IMAGES_DIR / f"{vm_name}.qcow2"
    subprocess.run(
        ["qemu-img", "convert", "-O", "qcow2", str(source_path), str(disk_path)],
        check=True, capture_output=True, text=True,
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
            raise ValueError("Le mot de passe ne doit pas contenir de retour à la ligne")
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


def create_cloudinit_reseed_iso(vm_name):
    """ISO cloud-init minimal pour un CLONE (voir clone_vm) : contrairement a
    create_cloudinit_iso(), ne recree pas le compte utilisateur (le disque
    clone en dispose deja -- copie du disque source -- et le mot de passe en
    clair de la VM d'origine n'est de toute facon jamais conserve nulle part
    par Hyperlite, impossible a reinjecter meme si on le voulait).
    Se contente de changer le hostname et de fournir un nouvel instance-id :
    cloud-init detecte alors une "nouvelle instance" au premier boot du clone
    et regenere de lui-meme les cles hote SSH (module `ssh` de cloud-init,
    comportement par defaut sur une instance jamais vue) et le hostname --
    exactement le risque identifie a l'audit (clone qui demarre avec les
    memes cles SSH hote et le meme hostname que l'original tant que rien ne
    force cloud-init a se re-executer)."""
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-reseed-"))
    try:
        user_data = workdir / "user-data"
        meta_data = workdir / "meta-data"
        user_data.write_text("#cloud-config\nhostname: {0}\nmanage_etc_hosts: true\n".format(vm_name))
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


def _host_cpu_caps_xml(conn):
    root = ET.fromstring(conn.getCapabilities())
    cpu_el = root.find("host/cpu")
    return ET.tostring(cpu_el, encoding="unicode") if cpu_el is not None else None


def _compute_migratable_cpu_xml():
    """Calcule un CPU 'plus petit denominateur commun' entre TOUS les nœuds
    du cluster (chantier 15) + l'hote local, pour qu'une VM creee reste
    migrable a chaud (chantier 27) vers n'importe quel nœud plutot que
    d'etre figee sur les fonctions exactes du CPU qui l'a creee ('host-
    model' seul fait exactement ca -- copie le modele le plus proche du
    CPU LOCAL une fois pour toutes a la creation, incompatible avec un
    nœud dont le CPU a ne serait-ce qu'une fonction en moins).

    Purement best-effort : retourne None (et build_domain_xml retombe sur
    host-model, comportement identique a avant ce chantier) des qu'un seul
    nœud existe ou que le calcul echoue pour QUELQUE RAISON QUE CE SOIT --
    jamais bloquant pour la creation de VM. BUG D'ENVIRONNEMENT REEL
    rencontre en testant avec un vrai second nœud physique (chantier 27) :
    deux hotes Intel Skylake, mais des VERSIONS DE QEMU/libvirt differentes
    embarquent des bases de modeles CPU differentes -- le modele exact
    renvoye par l'un ('Skylake-Client-v3') est carrement INCONNU de
    l'autre, `baselineCPU()` echoue avec 'Unknown CPU model'. Documente
    dans CLAUDE.md comme limite connue plutot que masque."""
    from app.core.cluster import list_nodes, build_libvirt_uri  # import tardif : cluster.py importe PROJDIR depuis CE module, cycle sinon

    try:
        nodes = list_nodes()
    except Exception:
        return None
    if not nodes:
        return None  # un seul nœud (le local) : host-model suffit, rien a calculer

    cpu_xmls = []
    local_conn = None
    try:
        local_conn = libvirt.open("qemu:///system")
        if local_conn is None:
            return None
        local_xml = _host_cpu_caps_xml(local_conn)
        if local_xml:
            cpu_xmls.append(local_xml)

        for node in nodes:
            remote_conn = None
            try:
                remote_conn = libvirt.open(build_libvirt_uri(node))
                if remote_conn is not None:
                    remote_xml = _host_cpu_caps_xml(remote_conn)
                    if remote_xml:
                        cpu_xmls.append(remote_xml)
            except libvirt.libvirtError:
                pass  # nœud injoignable : ignore, pas fatal pour le calcul global
            finally:
                if remote_conn is not None:
                    remote_conn.close()

        if len(cpu_xmls) < 2:
            return None  # aucun autre nœud reellement joignable

        return local_conn.baselineCPU(cpu_xmls, libvirt.VIR_CONNECT_BASELINE_CPU_MIGRATABLE)
    except libvirt.libvirtError:
        return None
    finally:
        if local_conn is not None:
            local_conn.close()


def build_domain_xml(vm_name, vcpu, memory_mb, disk_paths, cloudinit_path, network="default", iso_path=None, seed_iso_path=None, mac=None, kernel_path=None, initrd_path=None, kernel_cmdline=None):
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

    # L'ISO d'installation est place sur 'hda' (premier peripherique IDE) :
    # la directive kickstart `cdrom` (voir unattended_install.py) installe
    # depuis "le premier lecteur CD-ROM du systeme", sans scanner les autres
    # -- constate en test, avec l'ISO de reponses sur 'hda' et l'ISO
    # d'installation plus loin, Anaconda choisissait l'ISO de reponses
    # (aucune donnee installable) et echouait avec "Installation source not
    # set up". Le vrai media d'installation doit donc toujours occuper le
    # premier slot.
    iso_xml = ""
    if iso_path:
        iso_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='hda' bus='ide'/>
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
    # autoinstall, voir app/core/unattended_install.py). Attache comme DISQUE
    # (device='disk'), pas comme CD-ROM : deplacer l'ISO d'installation en
    # premiere position IDE n'a pas suffi -- constate en test, la directive
    # kickstart `cdrom` (voir unattended_install.py) continuait a echouer
    # avec "Installation source not set up" des que deux lecteurs CD-ROM
    # etaient presents, quel que soit leur ordre. En le presentant comme un
    # disque plutot qu'un CD-ROM, il ne peut plus etre confondu avec la
    # source d'installation (Anaconda ne le considere pas comme un lecteur
    # optique) tout en restant detectable par etiquette de volume (OEMDRV /
    # cidata) : c'est ce scan-la, et non le type de peripherique, qui importe
    # pour la detection du kickstart/autoinstall. Aucun <boot order> non
    # plus : jamais un media amorcable.
    # Pas de <readonly/> ici : libvirt refuse ce flag sur un disque IDE de
    # type 'disk' (seuls cdrom/floppy le supportent en IDE -- "unsupported
    # configuration: readonly ide disks are not supported", constate en
    # test). Sans consequence : ce fichier est une donnee jetable propre a
    # cette VM, pas un ISO partage entre plusieurs VM comme iso_path.
    seed_xml = ""
    if seed_iso_path:
        seed_xml = f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file='{seed_iso_path}'/>
      <target dev='hdb' bus='ide'/>
    </disk>"""

    # mac explicite (voir app/core/network_alloc.py) : permet de reserver une
    # IP fixe cote reseau libvirt avant meme de definir le domaine, plutot
    # que de laisser libvirt en generer une aleatoire.
    mac_xml = f"<mac address='{mac}'/>\n      " if mac else ""

    # kernel_path/initrd_path : demarrage direct d'un noyau/initrd extrait de
    # l'ISO (voir app/core/unattended_install.py::extract_casper_kernel),
    # utilise UNIQUEMENT pour le tout premier boot d'un autoinstall Ubuntu
    # (seul moyen d'ajouter le mot-cle "autoinstall" sur la ligne de commande
    # noyau et sauter la confirmation manuelle de Subiquity). IMPORTANT :
    # cet override doit etre retire du XML PERSISTANT une fois l'installation
    # terminee (voir vms.py::get_vm_provisioning, strip_install_boot_override
    # ci-dessous) -- sinon la VM rebooterait indefiniment sur l'installeur
    # live au lieu du systeme installe sur le disque, le <boot order> normal
    # n'etant jamais consulte tant que <kernel>/<initrd> sont presents.
    os_extra_xml = ""
    if kernel_path:
        cmdline_xml = f"\n    <cmdline>{kernel_cmdline}</cmdline>" if kernel_cmdline else ""
        os_extra_xml = f"""
    <kernel>{kernel_path}</kernel>
    <initrd>{initrd_path}</initrd>{cmdline_xml}"""

    # Chantier 27 (migration a chaud) : CPU "plus petit denominateur
    # commun" du cluster quand c'est calculable (2+ nœuds joignables avec
    # des CPU compatibles), sinon host-model classique -- voir la docstring
    # de _compute_migratable_cpu_xml pour le detail (et sa limite reelle
    # rencontree en test, documentee dans CLAUDE.md).
    cluster_cpu_xml = _compute_migratable_cpu_xml()
    cpu_xml = cluster_cpu_xml if cluster_cpu_xml else "<cpu mode='host-model'/>"

    return f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <currentMemory unit='MiB'>{memory_mb}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
  <os>
    <type arch='x86_64' machine='pc'>hvm</type>{os_extra_xml}
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  {cpu_xml}
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <controller type='scsi' model='virtio-scsi'/>{disks_xml}{iso_xml}{cloudinit_xml}{seed_xml}
    <interface type='network'>
      <source network='{network}'/>
      {mac_xml}<model type='virtio'/>
    </interface>
    <console type='pty'/>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
"""


def strip_install_boot_override(domain_xml):
    """Retire <kernel>/<initrd>/<cmdline> du XML d'un domaine, s'ils sont
    presents -- appele une fois un autoinstall Ubuntu termine (voir
    vms.py::get_vm_provisioning) pour que les demarrages suivants utilisent a
    nouveau le <boot order> normal (disque systeme) plutot que de rebooter
    indefiniment sur le noyau/initrd live extrait de l'ISO (voir
    build_domain_xml, parametre kernel_path). Ne modifie que le XML
    PERSISTANT (conn.defineXML) : le domaine deja demarre continue de
    tourner avec sa configuration live actuelle jusqu'au prochain
    redemarrage, sans interruption."""
    root = ET.fromstring(domain_xml)
    os_elem = root.find("os")
    if os_elem is None:
        return domain_xml
    changed = False
    for tag in ("kernel", "initrd", "cmdline"):
        elem = os_elem.find(tag)
        if elem is not None:
            os_elem.remove(elem)
            changed = True
    if not changed:
        return domain_xml
    return ET.tostring(root, encoding="unicode")
