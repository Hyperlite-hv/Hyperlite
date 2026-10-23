import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import firmware as fw
from app.core import paths
from app.core.safe_paths import safe_child

IMAGES_DIR = Path("/var/lib/libvirt/images")
BASE_IMAGE = IMAGES_DIR / "base" / "debian-12-generic-amd64.qcow2"
BASE_IMAGE_URL = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"

SSH_KEY_DIR = paths.SSH_DIR

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

# sd(a), sd(b), sd(c)...: used to name the virtio-scsi disks of a VM
SCSI_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def validate_name(name, kind="VM"):
    """Error message when `name` is not a valid resource name, else None (it RETURNS
    the message, it never raises). `kind` names the resource in the message: this
    rule is shared by VMs, containers, networks, pools, volumes, templates and snapshots."""
    # fullmatch, not match: `$` also matches before a trailing newline.
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return f"Invalid {kind} name (letters/digits/dashes, 2-63 characters, must start with a letter or a digit)"
    return None


def validate_username(username):
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        return "Invalid username (lowercase letters/digits/dashes/underscores, must start with a lowercase letter or _, 32 characters max)"
    return None


def _ensure_automation_keypair():
    """SSH key dedicated to Hyperlite (generated once on the server), injected into
    the cloud-init of every new VM so the web SSH terminal works without any
    password stored on the server side."""
    SSH_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = SSH_KEY_DIR / "hyperlite_automation"
    pub = SSH_KEY_DIR / "hyperlite_automation.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", "hyperlite-automation"],
            check=True,
            capture_output=True,
            text=True,
        )
        priv.chmod(0o600)
    return priv, pub


def get_or_create_automation_pubkey():
    _, pub = _ensure_automation_keypair()
    return pub.read_text().strip()


def get_automation_private_key_path():
    priv, _ = _ensure_automation_keypair()
    return priv


_base_image_lock = threading.Lock()


def ensure_base_image():
    """The cloud image every Debian VM starts from, downloaded once. It was written under its final name: a second
    VM created during the download saw the file "there" and started from a truncated image ("Image is not in qcow2
    format"), and a failed download left that truncated image for every later VM. Now one download at a time, under
    a temporary name, renamed only once qemu-img reads it as a qcow2 image."""
    BASE_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    with _base_image_lock:
        if BASE_IMAGE.exists():
            return BASE_IMAGE
        partial = BASE_IMAGE.with_name(BASE_IMAGE.name + ".part")
        try:
            subprocess.run(["wget", "-q", "-O", str(partial), BASE_IMAGE_URL], check=True)
            info = subprocess.run(
                ["qemu-img", "info", "--output=json", str(partial)], check=True, capture_output=True, text=True
            )
            if json.loads(info.stdout).get("format") != "qcow2":
                raise RuntimeError(f"The downloaded base image is not a qcow2 image ({BASE_IMAGE_URL})")
            partial.rename(BASE_IMAGE)
        finally:
            partial.unlink(missing_ok=True)
    return BASE_IMAGE


POOL_BASE_NAME = "hyperlite-base-debian-12.qcow2"


def ensure_pool_base_image(pool_dir):
    """The base cloud image inside a storage pool, copied there once. A VM's system disk only holds its changes over
    the base image: on a shared pool, a base image left in this node's /var/lib/libvirt/images could not be opened
    by the other nodes, and the VM could not migrate ("Cannot access backing file"). As Proxmox keeps templates in the
    storage of the disks based on them."""
    target = Path(pool_dir) / POOL_BASE_NAME
    with _base_image_lock:
        if target.exists():
            return target
    source = ensure_base_image()
    with _base_image_lock:
        if target.exists():  # another VM copied it meanwhile
            return target
        partial = target.with_name(target.name + ".part")
        try:
            shutil.copyfile(source, partial)
            partial.rename(target)
        finally:
            partial.unlink(missing_ok=True)
    return target


def disk_file(target_dir, vm_name, index):
    """Where a VM's disk `index` goes in a directory pool: <name>.qcow2, then <name>-2.qcow2..."""
    return safe_child(target_dir, f"{vm_name}.qcow2" if index == 0 else f"{vm_name}-{index + 1}.qcow2")


def _new_file(path):
    """A new VM's disk is a new file: never one there already, which may be another VM's disk (VM "web-2" and the
    second disk of "web", GHSA-vfmj-3v3j-48fq). The creation checks it first (app/routers/vms/create.py)."""
    if path.exists():
        raise ValueError(f"A disk file '{path.name}' already exists in this storage pool: choose another VM name")
    return path


def create_disk(vm_name, disk_gb, index=0, blank=False, target_dir=None):
    """Create a qcow2 disk for the VM. The disk at index 0 (system) is based on the
    default Debian cloud image; the following disks are always blank (extra
    storage). `blank=True` forces a blank disk 0 anyway, used when a VM boots
    from an installation ISO (see create_vm): there is then nothing to
    preinstall, and the user installs their own OS on it. `target_dir`: the
    path of the chosen storage pool, resolved by the caller through libvirt.
    It defaults to IMAGES_DIR (the 'default' pool), which leaves the historical
    behaviour unchanged."""
    target_dir = target_dir or IMAGES_DIR
    disk_path = _new_file(disk_file(target_dir, vm_name, index))
    if index == 0 and not blank:
        base = ensure_base_image() if Path(target_dir) == IMAGES_DIR else ensure_pool_base_image(Path(target_dir))
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(base),
                str(disk_path),
                f"{disk_gb}G",
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


def create_zvol_disk(zfs_pool, vm_name, index, disk_gb, blank=False, import_source=None):
    """zvol equivalent of create_disk()/create_disk_from_import() (ZFS pool managed
    by app/core/zfs_storage.py): creates a raw zvol instead of a qcow2 file, then
    writes the Debian cloud image or the imported disk to it with
    `qemu-img convert -O raw`. qemu-img can write directly to a block device, so
    no intermediate step is needed. Returns a DEVICE path
    (/dev/zvol/<pool>/<name>), not a file path, and build_domain_xml() must
    attach it as <disk type='block'>, not type='file'.

    `import_source`: the size is derived from the source disk (like
    create_disk_from_import, which also ignores any requested size). A zvol must
    be created with an explicit size in bytes, unlike a qcow2 which implicitly
    inherits the virtual size of its source file."""
    from app.core import zfs_storage

    zvol_name = vm_name if index == 0 else f"{vm_name}-{index + 1}"
    if import_source is not None:
        info = subprocess.run(
            ["qemu-img", "info", "--output=json", str(import_source)],
            check=True,
            capture_output=True,
            text=True,
        )
        import json

        virtual_size = json.loads(info.stdout)["virtual-size"]
        size_gb = max(1, -(-virtual_size // (1024**3)))  # arrondi au Go superieur
        dev_path = zfs_storage.create_zvol(zfs_pool, zvol_name, size_gb)
        subprocess.run(
            ["qemu-img", "convert", "-O", "raw", str(import_source), dev_path],
            check=True,
            capture_output=True,
            text=True,
        )
        return dev_path

    dev_path = zfs_storage.create_zvol(zfs_pool, zvol_name, disk_gb)
    if index == 0 and not blank:
        ensure_base_image()
        subprocess.run(
            ["qemu-img", "convert", "-O", "raw", str(BASE_IMAGE), dev_path], check=True, capture_output=True, text=True
        )
    return dev_path


def create_disk_from_import(vm_name, source_path, target_dir=None):
    """Create the system disk (index 0) of a VM from an already uploaded disk file
    (see app/routers/vm_disks.py) instead of the default Debian cloud image or
    a blank disk: this is the "import a VM from a disk" path of the creation
    form, an alternative to installing from an ISO. `qemu-img convert` detects the
    source format by itself (raw/vmdk/vdi/vhd/qcow2/...), nothing to tell it.
    `target_dir`: see create_disk()."""
    disk_path = _new_file(disk_file(target_dir or IMAGES_DIR, vm_name, 0))
    subprocess.run(
        ["qemu-img", "convert", "-O", "qcow2", str(source_path), str(disk_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return disk_path


def hash_guest_password(password):
    """The SHA-512 crypt of a guest password, as /etc/shadow holds it (as Proxmox puts it on its cloud-init drive). The
    drive stays attached to the VM, in its pool and its copies: it never holds the password in clear. Through openssl
    (a dependency of the package; Python 3.13 has no crypt module), the password on its standard input, never on a
    command line."""
    if any(c in password for c in ("\n", "\r")):
        raise ValueError("The password must not contain a line break")
    r = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True, text=True, check=True
    )
    hashed = r.stdout.strip()
    if not hashed.startswith("$6$"):
        raise RuntimeError("The password could not be hashed")
    return hashed


def protect_drive(path):
    """A cloud-init drive is private to root (libvirt hands it to QEMU when the VM starts): it holds the account's
    name, its SSH keys and its password's hash."""
    with contextlib.suppress(OSError):  # an NFS share that does not let root change modes keeps its own
        os.chmod(path, 0o600)


def protect_existing_drives(roots=None):
    """At start: the cloud-init drives written before they were made private (world-readable, 0644) become private
    too. Their content stays as it was (a drive made before passwords were hashed holds one in clear until the VM's
    password is changed: Cloud-Init tab). Returns how many were changed."""
    roots = roots or [IMAGES_DIR, Path("/var/lib/libvirt/hyperlite-pools")]
    changed = 0
    for root in roots:
        for drive in Path(root).glob("**/*-cloudinit.iso"):
            with contextlib.suppress(OSError):
                if drive.stat().st_mode & 0o077:
                    drive.chmod(0o600)
                    changed += 1
    return changed


# The drives Hyperlite generates for a VM, named after it: its cloud-init seed, and the unattended installation media
# of VMs created before 1.0.
GENERATED_ISO_SUFFIXES = ("-cloudinit.iso", "-oemdrv.iso", "-autoinstall.iso")


def generated_drives(root, vm_name):
    """The CD-ROM drives Hyperlite generated for this VM, as (disk element, path), from its parsed definition: named
    after the VM, in IMAGES_DIR or next to one of the VM's own disks, which is where they are written (create_vm puts
    the drive on the pool of the disks). Rebuilding the path from IMAGES_DIR alone missed every VM made on another
    pool; the name alone would also take an image of the ISO library that happens to carry it, which the service
    (root) would then rewrite or delete."""
    names = {f"{vm_name}{suffix}" for suffix in GENERATED_ISO_SUFFIXES}
    homes = {IMAGES_DIR}
    for disk in root.findall("./devices/disk[@device='disk']"):
        source = disk.find("source")
        if source is not None and source.get("file"):
            homes.add(Path(source.get("file")).parent)
    found = []
    for disk in root.findall("./devices/disk[@device='cdrom']"):
        source = disk.find("source")
        path = Path(source.get("file")) if source is not None and source.get("file") else None
        if path and path.name in names and path.parent in homes:
            found.append((disk, path))
    return found


def cloudinit_drive(root, vm_name):
    """The VM's cloud-init drive (path), or None: a VM installed from an ISO has none."""
    return next(
        (path for _disk, path in generated_drives(root, vm_name) if path.name == f"{vm_name}-cloudinit.iso"), None
    )


def create_cloudinit_iso(vm_name, username, password, ssh_pubkey=None, target_dir=None):
    # Temporary directory with restricted permissions (0700, created by mkdtemp), always cleaned up afterwards.
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-"))
    try:
        user_data = workdir / "user-data"
        meta_data = workdir / "meta-data"

        pwd_quoted = "'" + hash_guest_password(password) + "'"
        ud = [
            "#cloud-config",
            f"hostname: {vm_name}",
            "manage_etc_hosts: true",
            "users:",
            f"  - name: {username}",
            "    sudo: ALL=(ALL) NOPASSWD:ALL",
            "    shell: /bin/bash",
            f"    hashed_passwd: {pwd_quoted}",
            "    lock_passwd: false",
        ]
        if ssh_pubkey:
            ud.append("    ssh_authorized_keys:")
            ud.append(f"      - {ssh_pubkey}")
        ud += [
            "chpasswd:",
            "  expire: false",
            "ssh_pwauth: true",
            # Hyperlite Tools (see app/core/guest_agent.py). Without network access the install fails and
            # cloud-init goes on: the VM then simply works without the agent.
            "packages:",
            "  - qemu-guest-agent",
            "runcmd:",
            "  - [systemctl, enable, --now, qemu-guest-agent]",
        ]
        user_data.write_text("\n".join(ud) + "\n")
        meta_data.write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n")

        # safe_child: the name comes from a request; the drive is written, then its mode changed, in that directory only.
        iso_path = safe_child(target_dir or IMAGES_DIR, f"{vm_name}-cloudinit.iso")
        subprocess.run(
            ["cloud-localds", str(iso_path), str(user_data), str(meta_data)],
            check=True,
            capture_output=True,
            text=True,
        )
        protect_drive(iso_path)
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def create_cloudinit_reseed_iso(vm_name):
    """Minimal cloud-init ISO for a CLONE (see clone_vm). Unlike
    create_cloudinit_iso(), it does not recreate the user account (the cloned
    disk already has it, being a copy of the source disk, and the original VM's
    plain-text password is never kept anywhere by Hyperlite anyway, so it could
    not be reinjected even if we wanted to). It only changes the hostname and
    provides a new instance-id: cloud-init then detects a "new instance" at the
    clone's first boot and regenerates the SSH host keys itself (cloud-init's
    `ssh` module, the default behaviour on a never-seen instance) as well as the
    hostname. This is exactly the risk identified in the audit: a clone booting
    with the same SSH host keys and hostname as the original as long as nothing
    forces cloud-init to run again."""
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-reseed-"))
    try:
        user_data = workdir / "user-data"
        meta_data = workdir / "meta-data"
        user_data.write_text(f"#cloud-config\nhostname: {vm_name}\nmanage_etc_hosts: true\n")
        meta_data.write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n")

        iso_path = safe_child(IMAGES_DIR, f"{vm_name}-cloudinit.iso")
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
    """Compute a 'lowest common denominator' CPU across ALL the cluster nodes plus the
    local host, so a created VM stays live-migratable to any node instead of
    being pinned to the exact features of the CPU that created it ('host-model'
    alone does exactly that: it copies the model closest to the LOCAL CPU once
    and for all at creation, incompatible with a node whose CPU lacks even one
    feature).

    Purely best-effort: it returns None (and build_domain_xml falls back to
    host-model, the behaviour before this feature) as soon as only one node
    exists or the computation fails for ANY REASON, so it never blocks VM
    creation. A REAL ENVIRONMENT PROBLEM seen when testing with a real second
    physical node: two Intel Skylake hosts, purpose DIFFERENT QEMU/libvirt VERSIONS
    ship different CPU model databases. The exact model returned by one
    ('Skylake-Client-v3') is simply UNKNOWN to the other, and `baselineCPU()`
    fails with 'Unknown CPU model'. It is a known limitation, documented rather
    than hidden."""
    from app.core import node_health
    from app.core.cluster import (  # late import: cluster.py imports this module, a cycle otherwise
        libvirt_uri_from_here,
        list_nodes,
    )

    try:
        nodes = list_nodes()
    except Exception:
        return None
    if not nodes:
        return None  # a single node (the local one): host-model is enough, nothing to compute

    cpu_xmls = []
    local_conn = None
    try:
        local_conn = libvirt.open("qemu:///system")
        if local_conn is None:
            return None
        local_xml = _host_cpu_caps_xml(local_conn)
        if local_xml:
            cpu_xmls.append(local_xml)

        seen_down = node_health.down(nodes)
        for node in nodes:
            if node["name"] in seen_down:
                continue  # an unreachable node is ignored below anyway: not waited for
            remote_conn = None
            try:
                remote_conn = libvirt.open(libvirt_uri_from_here(node))
                if remote_conn is not None:
                    remote_xml = _host_cpu_caps_xml(remote_conn)
                    if remote_xml:
                        cpu_xmls.append(remote_xml)
            except libvirt.libvirtError:
                pass  # unreachable node: ignored, not fatal for the global computation
            finally:
                if remote_conn is not None:
                    remote_conn.close()

        if len(cpu_xmls) < 2:
            return None  # no other node is really reachable

        return local_conn.baselineCPU(cpu_xmls, libvirt.VIR_CONNECT_BASELINE_CPU_MIGRATABLE)
    except libvirt.libvirtError:
        # REAL ENVIRONMENT PROBLEM (see the docstring above, 'Unknown CPU model'): fall
        # back to a portable GENERIC CPU rather than host-model alone. host-model stays
        # pinned to the exact CPU of the creating node (incompatible with the other node
        # by construction, no migration possible without manually editing the XML),
        # whereas a generic CPU, even if less optimal, at least allows a REAL migration
        # without intervention. Verified working between two hosts before automating this
        # workaround (see _generic_portable_cpu_xml()).
        return _generic_portable_cpu_xml()
    finally:
        if local_conn is not None:
            local_conn.close()


def _generic_portable_cpu_xml():
    """Generic 'qemu64' CPU with svm/vmx explicitly disabled: a LAST RESORT fallback
    when baselineCPU() fails completely between the cluster nodes
    (incompatible CPU model databases, see _compute_migratable_cpu_xml above).
    `svm` (AMD virtualization) is among the features ENABLED BY DEFAULT in the
    'qemu64' model on this QEMU version, and it breaks startup on an Intel host
    unless explicitly disabled (seen when testing this workaround by hand before
    automating it here). `vmx` is disabled too for symmetry and caution: never
    seen as a real problem, purpose disabling a feature that is already absent is a
    harmless no-op."""
    return (
        "<cpu mode='custom' match='exact'>"
        "<model fallback='forbid'>qemu64</model>"
        "<feature policy='disable' name='svm'/>"
        "<feature policy='disable' name='vmx'/>"
        "</cpu>"
    )


def build_domain_xml(
    vm_name,
    vcpu,
    memory_mb,
    disk_paths,
    cloudinit_path,
    network="default",
    iso_path=None,
    mac=None,
    drivers_iso_path=None,
    disk_bus="scsi",
    interface_model="virtio",
    firmware=fw.BIOS,
):
    if disk_bus not in {"scsi", "sata"} or interface_model not in {"virtio", "e1000e"} or firmware not in fw.CHOICES:
        raise ValueError("Unsupported VM hardware profile")
    # Boot order PER DEVICE (<boot order='N'/> on each <disk>) rather than the
    # global <os><boot dev=.../></os> list: SeaBIOS does not reliably fall back
    # between several IDE CD-ROMs with the global list (it picks the first CD-ROM
    # found whatever it is, and gives up if it is not bootable, as the cloud-init or
    # driver media are). With an explicit per-device order, only the system disk and
    # the installation ISO carry a <boot order>, so no other medium is ever tried as a
    # boot device.
    # Each element of disk_paths is either a FILE path (str/Path, the historical
    # qcow2 behaviour) or a (path, 'block') tuple for a raw ZFS zvol (see
    # create_zvol_disk()). A VM can freely mix the two (e.g. a system disk on a zvol
    # plus an extra classic qcow2 disk).
    # <disk type='block'> + driver raw + <source dev=...> instead of
    # type='file'/<source file=...> is the XML difference that lets a raw block device
    # (a zvol today, a Ceph RBD tomorrow: the same shape, only the source of the path
    # changes) be attached like any other disk.
    disks_xml = ""
    for i, disk_entry in enumerate(disk_paths):
        if isinstance(disk_entry, (tuple, list)):
            disk_path, disk_kind = disk_entry
        else:
            disk_path, disk_kind = disk_entry, "file"
        dev = f"sd{SCSI_LETTERS[i]}"
        boot_order = " <boot order='1'/>" if i == 0 else ""
        if disk_kind == "block":
            disks_xml += f"""
    <disk type='block' device='disk'>
      <driver name='qemu' type='raw'/>
      <source dev='{disk_path}'/>
      <target dev='{dev}' bus='{disk_bus}'/>{boot_order}
    </disk>"""
        else:
            disks_xml += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='{dev}' bus='{disk_bus}'/>{boot_order}
    </disk>"""

    # The installation ISO is placed on 'hda' (the first IDE device): installers that look for their medium on
    # "the first CD-ROM drive of the system" (Anaconda) find it there, whatever other media the VM has.
    iso_xml = ""
    if iso_path:
        dev, bus = fw.cdrom_target("hda", firmware)
        iso_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='{dev}' bus='{bus}'/>
      <readonly/>
      <boot order='2'/>
    </disk>"""

    # cloudinit_path is optional: a VM booted from an installation ISO (blank system
    # disk, see create_vm) has no cloud-init to inject, and the OS and its user
    # account are created by hand in the installer. Never a <boot order>: this disk must never be
    # tried as a boot device, only read by the OS once started.
    cloudinit_xml = ""
    if cloudinit_path:
        dev, bus = fw.cdrom_target("hdc", firmware)
        cloudinit_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{cloudinit_path}'/>
      <target dev='{dev}' bus='{bus}'/>
      <readonly/>
    </disk>"""

    # Windows Setup needs the VirtIO SCSI driver before it can see the system disk.
    # Keep driver media on its own IDE drive, readable without a VirtIO driver,
    # and never include it in the boot order.
    drivers_xml = ""
    if drivers_iso_path:
        driver_cd = ET.Element("disk", {"type": "file", "device": "cdrom"})
        ET.SubElement(driver_cd, "driver", {"name": "qemu", "type": "raw"})
        ET.SubElement(driver_cd, "source", {"file": str(drivers_iso_path)})
        dev, bus = fw.cdrom_target("hdd", firmware)
        ET.SubElement(driver_cd, "target", {"dev": dev, "bus": bus})
        ET.SubElement(driver_cd, "readonly")
        drivers_xml = ET.tostring(driver_cd, encoding="unicode")

    # Explicit mac (see app/core/network_alloc.py): it allows reserving a fixed IP on
    # the libvirt network side before even defining the domain, instead of letting
    # libvirt generate a random one.
    mac_xml = f"<mac address='{mac}'/>\n      " if mac else ""

    # Live migration: the cluster's "lowest common denominator" CPU when it can be
    # computed (2+ reachable nodes with compatible CPUs), otherwise the classic
    # host-model. See the docstring of _compute_migratable_cpu_xml for the detail
    # (and its real limitation seen in testing).
    cluster_cpu_xml = _compute_migratable_cpu_xml()
    cpu_xml = cluster_cpu_xml if cluster_cpu_xml else "<cpu mode='host-model'/>"
    storage_controller = (
        "<controller type='scsi' model='virtio-scsi'/>" if disk_bus == "scsi" else "<controller type='sata' index='0'/>"
    )

    return f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <currentMemory unit='MiB'>{memory_mb}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
  {fw.os_xml(firmware)}
  {fw.features_xml(firmware)}
  {cpu_xml}
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    {storage_controller}{disks_xml}{iso_xml}{cloudinit_xml}{drivers_xml}
    <interface type='network'>
      <source network='{network}'/>
      {mac_xml}<model type='{interface_model}'/>
    </interface>
    <console type='pty'/>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>{fw.tpm_xml(firmware)}
  </devices>
</domain>
"""
