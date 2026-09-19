import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core.safe_paths import safe_child

IMAGES_DIR = Path("/var/lib/libvirt/images")
BASE_IMAGE = IMAGES_DIR / "base" / "debian-12-generic-amd64.qcow2"
BASE_IMAGE_URL = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"

# Project root directory (app/core/vm_builder.py -> app/core -> app -> root),
# computed dynamically so it works whatever the installation path.
PROJDIR = Path(__file__).resolve().parents[2]
SSH_KEY_DIR = PROJDIR / "data" / "ssh"

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

# sd(a), sd(b), sd(c)...: used to name the virtio-scsi disks of a VM
SCSI_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def validate_name(name):
    if not NAME_RE.match(name):
        return "Invalid VM name (letters/digits/dashes, 2-63 characters, must start with a letter or a digit)"
    return None


def validate_username(username):
    if not USERNAME_RE.match(username):
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


def ensure_base_image():
    BASE_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not BASE_IMAGE.exists():
        subprocess.run(
            ["wget", "-q", "-O", str(BASE_IMAGE), BASE_IMAGE_URL],
            check=True,
        )
    return BASE_IMAGE


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
    disk_path = target_dir / (f"{vm_name}.qcow2" if index == 0 else f"{vm_name}-{index + 1}.qcow2")
    if index == 0 and not blank:
        ensure_base_image()
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(BASE_IMAGE),
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
    form, an alternative to ISO + kickstart. `qemu-img convert` detects the
    source format by itself (raw/vmdk/vdi/vhd/qcow2/...), nothing to tell it.
    `target_dir`: see create_disk()."""
    target_dir = target_dir or IMAGES_DIR
    disk_path = target_dir / f"{vm_name}.qcow2"
    subprocess.run(
        ["qemu-img", "convert", "-O", "qcow2", str(source_path), str(disk_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return disk_path


def create_cloudinit_iso(vm_name, username, password, ssh_pubkey=None, target_dir=None):
    # Temporary directory with restricted permissions (0700, created by mkdtemp),
    # always cleaned up afterwards: user-data contains the VM's plain-text password
    # and must not outlive this function on the host disk.
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-"))
    try:
        user_data = workdir / "user-data"
        meta_data = workdir / "meta-data"

        if any(c in password for c in ("\n", "\r")):
            raise ValueError("The password must not contain a line break")
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

        iso_path = (target_dir or IMAGES_DIR) / f"{vm_name}-cloudinit.iso"
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
    physical node: two Intel Skylake hosts, but DIFFERENT QEMU/libvirt VERSIONS
    ship different CPU model databases. The exact model returned by one
    ('Skylake-Client-v3') is simply UNKNOWN to the other, and `baselineCPU()`
    fails with 'Unknown CPU model'. It is a known limitation, documented rather
    than hidden."""
    from app.core.cluster import (  # late import: cluster.py imports PROJDIR from THIS module, a cycle otherwise
        build_libvirt_uri,
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

        for node in nodes:
            remote_conn = None
            try:
                remote_conn = libvirt.open(build_libvirt_uri(node))
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
    seen as a real problem, but disabling a feature that is already absent is a
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
    seed_iso_path=None,
    mac=None,
    kernel_path=None,
    initrd_path=None,
    kernel_cmdline=None,
):
    # Boot order PER DEVICE (<boot order='N'/> on each <disk>) rather than the
    # global <os><boot dev=.../></os> list: SeaBIOS does not reliably fall back
    # between several IDE CD-ROMs with the global list (it picks the first CD-ROM
    # found whatever it is, and gives up if it is not bootable, which failed as soon
    # as the OEMDRV/cidata answers ISO, never meant to be booted, came before the real
    # installation ISO). With an explicit per-device order, only the system disk and
    # the installation ISO carry a <boot order>, and the answers ISO carries none, so
    # it is never tried as a boot device.
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
      <target dev='{dev}' bus='scsi'/>{boot_order}
    </disk>"""
        else:
            disks_xml += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='{dev}' bus='scsi'/>{boot_order}
    </disk>"""

    # The installation ISO is placed on 'hda' (the first IDE device): the kickstart
    # `cdrom` directive (see unattended_install.py) installs from "the first CD-ROM
    # drive of the system" without scanning the others. Seen in testing: with the
    # answers ISO on 'hda' and the installation ISO further along, Anaconda picked the
    # answers ISO (no installable data) and failed with "Installation source not set
    # up". The real installation medium must therefore always occupy the first slot.
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

    # cloudinit_path is optional: a VM booted from an installation ISO (blank system
    # disk, see create_vm) has no cloud-init to inject, and the OS and its user
    # account are created manually by the installer (or automatically through
    # seed_iso_path, see just below). Never a <boot order>: this disk must never be
    # tried as a boot device, only read by the OS once started.
    cloudinit_xml = ""
    if cloudinit_path:
        cloudinit_xml = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{cloudinit_path}'/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
    </disk>"""

    # seed_iso_path: a small answers ISO (OEMDRV/kickstart or cidata/autoinstall, see
    # app/core/unattended_install.py). It is attached as a DISK (device='disk'), not
    # as a CD-ROM: moving the installation ISO to the first IDE position was not
    # enough. Seen in testing, the kickstart `cdrom` directive kept failing with
    # "Installation source not set up" as soon as two CD-ROM drives were present,
    # whatever their order. Presented as a disk rather than a CD-ROM, it can no longer
    # be mistaken for the installation source (Anaconda does not consider it an
    # optical drive) while staying detectable by volume label (OEMDRV / cidata): it is
    # that scan, not the device type, that matters for kickstart/autoinstall
    # detection. No <boot order> either: never a bootable medium.
    # No <readonly/> here: libvirt refuses that flag on an IDE disk of type 'disk'
    # (only cdrom/floppy support it on IDE: "unsupported configuration: readonly ide
    # disks are not supported", seen in testing). It makes no difference: this file is
    # throwaway data specific to this VM, not an ISO shared between several VMs like
    # iso_path.
    seed_xml = ""
    if seed_iso_path:
        seed_xml = f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file='{seed_iso_path}'/>
      <target dev='hdb' bus='ide'/>
    </disk>"""

    # Explicit mac (see app/core/network_alloc.py): it allows reserving a fixed IP on
    # the libvirt network side before even defining the domain, instead of letting
    # libvirt generate a random one.
    mac_xml = f"<mac address='{mac}'/>\n      " if mac else ""

    # kernel_path/initrd_path: direct boot of a kernel/initrd extracted from the ISO
    # (see app/core/unattended_install.py::extract_casper_kernel), used ONLY for the
    # very first boot of an Ubuntu autoinstall (the only way to add the "autoinstall"
    # keyword to the kernel command line and skip Subiquity's manual confirmation).
    # IMPORTANT: this override must be removed from the PERSISTENT XML once the
    # installation is finished (see vms.py::get_vm_provisioning and
    # strip_install_boot_override below), otherwise the VM would reboot forever into
    # the live installer instead of the system installed on the disk, since the normal
    # <boot order> is never consulted while <kernel>/<initrd> are present.
    os_extra_xml = ""
    if kernel_path:
        cmdline_xml = f"\n    <cmdline>{kernel_cmdline}</cmdline>" if kernel_cmdline else ""
        os_extra_xml = f"""
    <kernel>{kernel_path}</kernel>
    <initrd>{initrd_path}</initrd>{cmdline_xml}"""

    # Live migration: the cluster's "lowest common denominator" CPU when it can be
    # computed (2+ reachable nodes with compatible CPUs), otherwise the classic
    # host-model. See the docstring of _compute_migratable_cpu_xml for the detail
    # (and its real limitation seen in testing).
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
    """Remove <kernel>/<initrd>/<cmdline> from a domain's XML, if present. Called
    once an Ubuntu autoinstall has finished (see vms.py::get_vm_provisioning) so
    that the following boots use the normal <boot order> (the system disk) again
    instead of rebooting forever into the live kernel/initrd extracted from the
    ISO (see build_domain_xml, the kernel_path parameter). It only modifies the
    PERSISTENT XML (conn.defineXML): the already started domain keeps running
    with its current live configuration until the next restart, with no
    interruption."""
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
