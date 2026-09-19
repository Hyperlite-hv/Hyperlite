"""Unattended installation from an ISO (Kickstart for the RHEL family,
autoinstall/NoCloud for Ubuntu): builds a small "answers ISO" that the
installer detects by itself at boot to create the user account and install
the Hyperlite automation SSH key, exactly as the cloud-init of Debian VMs
already does.

Kickstart (RHEL and derivatives) needs no boot argument: Anaconda detects the
OEMDRV volume by itself at startup. Ubuntu/autoinstall, on the other hand,
needs the "autoinstall" keyword on the kernel command line to skip its single
manual confirmation ("Continue with autoinstall?"). See extract_casper_kernel
below, which extracts /casper/vmlinuz and /casper/initrd from the ISO to boot
them directly through libvirt (<os><kernel>/<initrd>/<cmdline>), the same
principle as the boot menu built for the Hyperlite Appliance installer (see
installer/build-iso.sh).

Known limitation: each OS family has its own answer file format; only RHEL
(and Anaconda derivatives: CentOS/Rocky/AlmaLinux/Fedora) and Ubuntu (the
Subiquity installer, "live-server" ISO) are covered. An unrecognized ISO falls
back to the existing manual installation (see vms.create_vm)."""

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.core.passwords import sha512_crypt_hash
from app.core.safe_paths import safe_child

from .vm_builder import IMAGES_DIR

KICKSTART_FAMILIES = ("rhel", "centos", "rocky", "almalinux", "alma-", "fedora")
AUTOINSTALL_FAMILIES = ("ubuntu",)

# Cache of the extracted casper kernels/initrds: one extraction per ISO (reused
# for every VM created from the same file), not one per VM creation. It lives
# under data/ like the other caches and data owned by Hyperlite (data/isos,
# data/ssh, data/tls), not under libvirt's system directory.
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
    return sha512_crypt_hash(password)


def build_kickstart_iso(vm_name, username, password, ssh_pubkey):
    """ISO labelled OEMDRV containing ks.cfg: Anaconda (RHEL/CentOS/Rocky/Alma/
    Fedora) automatically detects an OEMDRV volume at boot and uses it as the
    kickstart source, with no boot argument to provide. The SSH key is written
    through %post rather than the `sshkey` directive (not supported by every
    Anaconda version, whereas %post has worked everywhere since RHEL 6)."""
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

        iso_path = safe_child(IMAGES_DIR, f"{vm_name}-oemdrv.iso")
        subprocess.run(
            ["genisoimage", "-o", str(iso_path), "-V", "OEMDRV", "-r", "-J", str(ks_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def extract_casper_kernel(iso_path):
    """Extract /casper/vmlinuz and /casper/initrd from the Ubuntu live-server ISO
    to boot them directly through libvirt (<os><kernel>/<initrd>), adding
    "autoinstall" to the command line. This is the only way to skip Subiquity's
    single manual confirmation ("Continue with autoinstall?"): the keyword must
    be present from kernel boot, not only in the answers ISO (see
    build_autoinstall_iso). The command line was checked directly in the real
    grub.cfg of the Ubuntu 26.04 ISO: `linux /casper/vmlinuz  ---` +
    `initrd /casper/initrd`, no other parameter required.

    The extraction is cached by ISO file name (reused for every VM created
    from the same file) instead of being redone at each VM creation: it reads
    ~200 MB from an ISO of several GB, which is not instantaneous.

    Returns (kernel_path, initrd_path)."""
    cache_dir = safe_child(CASPER_CACHE_DIR, Path(iso_path).stem)
    kernel_path = cache_dir / "vmlinuz"
    initrd_path = cache_dir / "initrd"
    if kernel_path.exists() and initrd_path.exists():
        return kernel_path, initrd_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    for member, dest in (("/casper/vmlinuz", kernel_path), ("/casper/initrd", initrd_path)):
        subprocess.run(
            ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", member, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    return kernel_path, initrd_path


def build_autoinstall_iso(vm_name, username, password, ssh_pubkey):
    """NoCloud ISO (label cidata, the same cloud-localds tool as the Debian
    cloud-init) containing an autoinstall.yaml: Subiquity (Ubuntu's
    "live-server" installer) detects this source by itself through the volume
    label. The "autoinstall" keyword must ALSO be present on the kernel command
    line (see extract_casper_kernel) to skip the single manual confirmation
    ("Continue with autoinstall?"): the presence of this file alone is not
    enough."""
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

        iso_path = safe_child(IMAGES_DIR, f"{vm_name}-autoinstall.iso")
        subprocess.run(
            ["cloud-localds", str(iso_path), str(workdir / "user-data"), str(workdir / "meta-data")],
            check=True,
            capture_output=True,
            text=True,
        )
        return iso_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_seed_iso(os_family, vm_name, username, password, ssh_pubkey):
    if os_family == "kickstart":
        return build_kickstart_iso(vm_name, username, password, ssh_pubkey)
    if os_family == "autoinstall":
        return build_autoinstall_iso(vm_name, username, password, ssh_pubkey)
    raise ValueError(f"Unsupported OS family: {os_family}")
