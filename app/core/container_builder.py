"""Containers: building and configuring LXC containers through libvirt's
native LXC driver (lxc:///system, distinct from the qemu:///system used for
VMs, see app/core/libvirt_utils.py).

The principle mirrors app/core/vm_builder.py for VMs:
- A single base image built with debootstrap (Debian 12), cached under
  CONTAINERS_DIR/base/. Re-bootstrapping for every container creation would take
  several minutes and need an internet connection each time, exactly the same
  reasoning as vm_builder.ensure_base_image() for the VMs' Debian cloud image.
- Each new container clones this base with a fast local copy (cp -a), then
  customizes it (hostname, user account, password, automation SSH key) directly
  on the files through `chroot`. No cloud-init or kickstart/preseed is needed
  here: the container's filesystem is directly accessible from the host before
  its first boot.
- The container starts with /sbin/init (systemd) as PID 1: a real minimal Debian
  system, not just a shell, to benefit from standard service management (sshd,
  networking through systemd-networkd) exactly like a classically installed
  system.

"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.passwords import sha512_crypt_hash

CONTAINERS_DIR = Path("/var/lib/libvirt/containers")
BASE_ROOTFS = CONTAINERS_DIR / "base" / "debian-12"
# Images pulled from a registry (Docker Hub by default, or any OCI registry:
# "ghcr.io/foo/bar:tag" works too): cached separately from the local base above,
# one per requested image reference.
PULLED_IMAGES_DIR = CONTAINERS_DIR / "base" / "images"
DEBOOTSTRAP_SUITE = "bookworm"
DEBOOTSTRAP_MIRROR = "http://deb.debian.org/debian"
# Default variant (NOT minbase): it guarantees that systemd and its dependencies
# arrive cleanly through Debian's standard priorities instead of having to list
# them all by hand and risk forgetting one. Only the packages really specific to
# Hyperlite (ssh, sudo) are added explicitly on top. NO ifupdown/isc-dhcp-client:
# libvirtd's AppArmor profile on a standard Debian host forbids sending a signal
# to dhclient (`apparmor="DENIED" ... signal=term ... peer="/sbin/dhclient"`,
# seen in testing), so destroying a container whose interface was configured by
# dhclient fails silently on the kernel side and leaves the process orphaned.
# systemd-networkd (already provided by the systemd package, see
# configure_container_rootfs) has its own built-in DHCP client, with no external
# binary to confine separately and therefore no conflict.
DEBOOTSTRAP_INCLUDE = "openssh-server,sudo"


def ensure_base_rootfs():
    """Build the containers' base image (once). A slow operation (several minutes,
    network download): called explicitly before the first container creation,
    not every time; see create_container_rootfs."""
    if (BASE_ROOTFS / "bin" / "sh").exists():
        return BASE_ROOTFS
    BASE_ROOTFS.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(BASE_ROOTFS, ignore_errors=True)
    subprocess.run(
        [
            "debootstrap",
            "--arch=amd64",
            f"--include={DEBOOTSTRAP_INCLUDE}",
            DEBOOTSTRAP_SUITE,
            str(BASE_ROOTFS),
            DEBOOTSTRAP_MIRROR,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return BASE_ROOTFS


def container_rootfs_path(name):
    return CONTAINERS_DIR / name


# ---- Images pulled from a registry (Docker Hub or other) ----
#
# skopeo (fetches the image, no Docker daemon) + umoci (unpacks the OCI layers
# into a usable filesystem). Verified in practice on this host: the official
# "debian:12" image from the Hub, once unpacked, does NOT have systemd (no
# /sbin/init, since Docker images are designed for a single process, not a full
# OS) but DOES have a real package manager (apt-get), which is enough to install
# systemd/ssh/sudo afterwards, exactly like for the local debootstrap base.
# REALLY minimal images (scratch, distroless, without a package manager) are not
# supported: bootstrap_os_container raises an explicit error rather than
# silently producing an unusable container.
IMAGE_REF_SAFE_RE = re.compile(r"[^a-zA-Z0-9]+")


def _image_cache_dir(image_ref):
    return PULLED_IMAGES_DIR / IMAGE_REF_SAFE_RE.sub("_", image_ref)


def pull_image_rootfs(image_ref):
    """Pull an image from an OCI/Docker registry (Docker Hub by default when no
    registry is given in the reference, like `docker pull`) and unpack it into
    a filesystem, cached by exact reference (a `nginx:latest` requested again
    later reuses the cache, so moving tags such as `latest` are NOT rechecked on
    every creation, the same tradeoff as the local debootstrap base)."""
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
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["umoci", "unpack", "--image", f"{oci_dir}:latest", str(cache_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not rootfs.exists():
        raise ValueError(f"Image '{image_ref}' fetched but the unpacking is invalid (no rootfs/)")
    _ensure_dev_nodes(rootfs)
    return rootfs


def _ensure_dev_nodes(rootfs):
    """Create the essential /dev device nodes in a rootfs pulled from a registry. A
    Docker image does NOT contain them (the Docker runtime provides them itself
    when the container starts, not the image). Seen in real testing: /dev/null
    there is a plain EMPTY FILE of 0 bytes, not a real device (`c 1 3`), and
    gpg/apt-key and many other tools fail as soon as they try to write to it
    ("cannot create /dev/null: Permission denied"). debootstrap (the local base)
    already creates them, so this fix only concerns images pulled from a
    registry."""
    dev = rootfs / "dev"
    dev.mkdir(exist_ok=True)
    nodes = [
        ("null", "c", 1, 3, 0o666),
        ("zero", "c", 1, 5, 0o666),
        ("full", "c", 1, 7, 0o666),
        ("random", "c", 1, 8, 0o666),
        ("urandom", "c", 1, 9, 0o666),
        ("tty", "c", 5, 0, 0o666),
        ("console", "c", 5, 1, 0o600),
        ("ptmx", "c", 5, 2, 0o666),
    ]
    for devname, kind, major, minor, mode in nodes:
        path = dev / devname
        if path.is_symlink() or path.exists():
            if path.is_file() and not path.is_symlink():
                path.unlink()
            else:
                continue
        subprocess.run(
            ["mknod", "-m", oct(mode)[2:], str(path), kind, str(major), str(minor)],
            check=True,
            capture_output=True,
            text=True,
        )


def detect_package_family(rootfs):
    """Guess the package manager of a rootfs pulled from an external image (unlike
    the local base, whose family is already known). Debian/Ubuntu (apt) and
    Alpine (apk) cover the vast majority of real public images: most "minimal"
    images such as nginx/redis/postgres are still based on one of the two under
    the hood, not on totally bare "scratch" images."""
    if (rootfs / "usr" / "bin" / "apt-get").exists() or (rootfs / "usr" / "bin" / "dpkg").exists():
        return "apt"
    if (
        (rootfs / "sbin" / "apk").exists()
        or (rootfs / "usr" / "sbin" / "apk").exists()
        or (rootfs / "usr" / "bin" / "apk").exists()
    ):
        return "apk"
    return None


def bootstrap_os_container(rootfs):
    """Install systemd (or Alpine's native init, already provided by alpine-base) +
    openssh-server + sudo in a rootfs pulled from a registry. Public images
    normally have neither (they are designed to run ONE process, not a full OS
    with SSH access like Hyperlite containers). Temporary networking: the
    host's resolv.conf is copied for the duration of the installation (the
    pulled image does not necessarily have a valid one), then replaced by the
    final static resolv.conf in configure_container_rootfs."""
    family = detect_package_family(rootfs)
    if family is None:
        raise ValueError(
            "This image has no recognized package manager (apt/apk):"
            "probably a minimal single-process image (scratch/distroless), "
            "it is not compatible with the Hyperlite container model (full system + SSH access)."
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
            # Chicken-and-egg problem seen in real testing: "apt-get update" fails right away
            # with "gpgv...required for verification". The minimal official Docker images do
            # not have gnupg preinstalled, yet gnupg is precisely what would be needed to
            # verify the repositories and install anything else. Verification is disabled
            # ONLY while installing gnupg itself (official Debian repositories, already
            # referenced as is in the image, not a source added by Hyperlite), never for the
            # packages installed afterwards.
            insecure = ["-o", "Acquire::AllowInsecureRepositories=true", "-o", "APT::Get::AllowUnauthenticated=true"]
            subprocess.run(
                ["chroot", str(rootfs), "apt-get", *insecure, "update"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            subprocess.run(
                ["chroot", str(rootfs), "apt-get", "install", "-y", *insecure, "--no-install-recommends", "gnupg"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            subprocess.run(
                ["chroot", str(rootfs), "apt-get", "update"], check=True, capture_output=True, text=True, env=env
            )
            subprocess.run(
                [
                    "chroot",
                    str(rootfs),
                    "apt-get",
                    "install",
                    "-y",
                    "--no-install-recommends",
                    "systemd",
                    "systemd-sysv",
                    "openssh-server",
                    "sudo",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        elif family == "apk":
            subprocess.run(["chroot", str(rootfs), "apk", "update"], check=True, capture_output=True, text=True)
            # EXPLICIT openrc: the official "alpine" image from Docker Hub does NOT have
            # openrc preinstalled (verified in real testing: /etc/init.d/ was empty after
            # configuration, "sshd"/"networking" stayed broken symlinks pointing to scripts
            # that do not exist, and the container booted as far as getty but with no network
            # and no sshd). Unlike the full Alpine appliance used by the VMs' kickstart
            # (unattended_install.py), a minimal Docker image only has busybox + apk. Also
            # shadow/bash: BusyBox provides "adduser" (not "useradd") and no bash by default.
            # They are installed so that configure_container_rootfs (useradd/chpasswd -e/bash)
            # works identically whatever the family, without duplicating it per package
            # family.
            subprocess.run(
                ["chroot", str(rootfs), "apk", "add", "--no-cache", "openrc", "openssh", "sudo", "shadow", "bash"],
                check=True,
                capture_output=True,
                text=True,
            )
    finally:
        # Restored to the image's original resolv.conf (or removed if there was none):
        # configure_container_rootfs sets its own right afterwards anyway, but there is no
        # reason to leave a leak of the HOST's resolv.conf in the final rootfs by accident.
        if original_resolv is not None:
            target_resolv.write_bytes(original_resolv)
        else:
            target_resolv.unlink(missing_ok=True)

    return family


def create_container_rootfs(name, image=None):
    """Fast clone (local copy, no network) of the base image, either the local one
    (debootstrap, the default) or one pulled from an external registry
    (`image`, e.g. "ubuntu:22.04", "alpine:3.19", "ghcr.io/foo/bar:tag"), for a
    new container. `cp -a` before the configuration chroot avoids
    reimplementing a faithful recursive copy (permissions, symbolic links,
    special /dev devices) by hand in Python.

    Returns (rootfs_path, family): the family ("apt" or "apk") is passed as is
    to configure_container_rootfs, which needs it for the network/service
    configuration (systemd vs OpenRC)."""
    if image:
        base = pull_image_rootfs(image)
        family = detect_package_family(base)
        # Bootstrap systemd/ssh/sudo ONCE on the cached image, not for every container
        # cloned from it: the same reasoning as debootstrap for the local base.
        marker = base.parent / ".hyperlite-bootstrapped"
        if not marker.exists():
            bootstrap_os_container(base)
            marker.touch()
    else:
        base = ensure_base_rootfs()
        family = "apt"

    dest = container_rootfs_path(name)
    if dest.exists():
        raise ValueError(f"Container '{name}' already has a filesystem on disk")
    subprocess.run(["cp", "-a", str(base), str(dest)], check=True, capture_output=True, text=True)
    return dest, family


def delete_container_rootfs(name):
    shutil.rmtree(container_rootfs_path(name), ignore_errors=True)


# Container clone and backup. CONFIRMED in testing (virsh -c lxc:///system
# snapshot-create-as) that the LXC driver of this libvirt version does NOT support
# virDomainSnapshotCreateXML at all ("this function is not supported by the
# connection driver"), so no instantaneous snapshot is possible as for VMs (a
# qcow2 disk). Two filesystem-based mechanisms are used instead, consistent with a
# container being a plain directory, not a virtual disk:
# - clone_container_rootfs(): a complete copy (the equivalent of VM cloning),
#   which requires the container to be STOPPED (the same rule as VM cloning).
# - backup/restore: a tar archive (the equivalent of VM backups, but file by file
#   rather than a qcow2 disk).


def clone_container_rootfs(name, new_name):
    """Complete copy of the rootfs (cp -a, with the same guarantees as
    create_container_rootfs: permissions, symbolic links, special devices), THEN
    reset whatever must never be shared between original and clone, the same
    reasoning as VM cloning: SSH host keys and machine-id identical between the
    two as long as nothing forces their regeneration. It does keep the existing
    user account and password (the copied rootfs already has them), exactly as
    VM cloning does not recreate the account either."""
    src = container_rootfs_path(name)
    if not src.exists():
        raise ValueError(f"Filesystem of container '{name}' not found")
    dest = container_rootfs_path(new_name)
    if dest.exists():
        raise ValueError(f"Container '{new_name}' already has a filesystem on disk")
    subprocess.run(["cp", "-a", str(src), str(dest)], check=True, capture_output=True, text=True)
    _reset_container_identity(dest, new_name)
    return dest


def _reset_container_identity(rootfs, new_hostname):
    """Reset everything that must never be shared between two containers copied from
    the same rootfs (a clone, or the restore of a backup under a NEW name, see
    restore_container_rootfs): the same reasoning as VM cloning. It does keep
    the existing user account and password (already on the copied rootfs)."""
    (rootfs / "etc" / "hostname").write_text(new_hostname + "\n")
    hosts_path = rootfs / "etc" / "hosts"
    if hosts_path.exists():
        lines = hosts_path.read_text().splitlines(keepends=True)
        lines = [line for line in lines if "127.0.1.1" not in line]
        hosts_path.write_text(f"127.0.1.1 {new_hostname}\n" + "".join(lines))

    # SSH host keys. Deleting the keys and relying on ssh.service to regenerate them
    # at start-up does NOT work on this rootfs (the debootstrap base), confirmed by a
    # real SSH connection refused right after the clone started ("Connection
    # refused": sshd does not start at all without keys present). The base's keys were
    # generated ONCE by the openssh-server package's postinst at debootstrap time
    # (ssh-keygen -A, run at INSTALLATION, not at every start), so it must be redone
    # explicitly here rather than assuming a regeneration mechanism that does not
    # exist in this environment.
    ssh_dir = rootfs / "etc" / "ssh"
    if ssh_dir.exists():
        for key_file in ssh_dir.glob("ssh_host_*"):
            key_file.unlink(missing_ok=True)
        subprocess.run(["chroot", str(rootfs), "ssh-keygen", "-A"], check=True, capture_output=True, text=True)

    # Empty machine-id (NOT removed: systemd wants it present but empty to trigger a
    # regeneration at first boot, see machine-id(5)): avoids D-Bus/journald identifiers
    # shared between the two.
    machine_id = rootfs / "etc" / "machine-id"
    if machine_id.exists():
        machine_id.write_text("")


def backup_container_rootfs(name, dest_tar_path):
    """Complete tar archive of the rootfs: the equivalent of VM backups but file by
    file (there is no qcow2 disk to copy for a container). The container must be
    STOPPED, which the caller ensures (consistency of the archived content, the
    same rule as cloning); it is not revalidated here."""
    src = container_rootfs_path(name)
    if not src.exists():
        raise ValueError(f"Filesystem of container '{name}' not found")
    subprocess.run(
        ["tar", "-czf", str(dest_tar_path), "-C", str(src.parent), src.name],
        check=True,
        capture_output=True,
        text=True,
    )


def restore_container_rootfs(tar_path, name, original_name=None):
    """Restore an archive made by backup_container_rootfs() to a container rootfs,
    either OVERWRITING the existing rootfs of the same name (an "in place"
    restore, the container already stopped/deleted before the call) or to a
    different name (a restore "under a new name", the same principle as
    restore_backup(mode='new') for VMs).

    `original_name` (the container's name at the time of THE BACKUP): without
    resetting the identity when `name` differs from the original, the restored
    container kept the OLD hostname/SSH host keys inside the rootfs (confirmed
    over SSH: `hostname` still returned the old name), inconsistent with its new
    libvirt domain name. It applies the same reset as clone_container_rootfs(),
    but ONLY if the name really changes (an "in place" restore under the SAME
    name does not need it, the identity is already correct for that name)."""
    dest = container_rootfs_path(name)
    if dest.exists():
        raise ValueError(f"Container '{name}' already has a filesystem on disk")
    with tempfile.TemporaryDirectory(dir=str(CONTAINERS_DIR)) as tmp:
        subprocess.run(["tar", "-xzf", str(tar_path), "-C", tmp], check=True, capture_output=True, text=True)
        extracted = list(Path(tmp).iterdir())
        if len(extracted) != 1 or not extracted[0].is_dir():
            raise ValueError("Invalid archive (unexpected structure)")
        shutil.move(str(extracted[0]), str(dest))
    if original_name and original_name != name:
        _reset_container_identity(dest, name)
    return dest


def _hash_password(password):
    return sha512_crypt_hash(password)


def configure_container_rootfs(rootfs, hostname, username, password, ssh_pubkey, family="apt"):
    """Customize a freshly cloned rootfs: exactly the equivalent of the RHEL
    kickstart %post or the Debian late_command (see app/core/unattended_install.py),
    but applied directly on the filesystem through `chroot` since it is already
    accessible from the host, with no first boot needed to run anything.
    `family` ("apt" or "apk", see detect_package_family) only changes the
    network/service configuration (systemd vs OpenRC): the user account,
    password, sudo and SSH key are identical in both cases (shadow/bash/sudo are
    installed on both families, see bootstrap_os_container)."""
    pwd_hash = _hash_password(password)

    (rootfs / "etc" / "hostname").write_text(hostname + "\n")
    hosts_path = rootfs / "etc" / "hosts"
    existing_hosts = hosts_path.read_text() if hosts_path.exists() else ""
    hosts_path.write_text(f"127.0.0.1 localhost\n127.0.1.1 {hostname}\n{existing_hosts}")

    if family == "apk":
        # Alpine (OpenRC, not systemd, see bootstrap_os_container): DHCP through busybox
        # udhcpc/classic ifupdown, with no separate dhclient (the binary specifically
        # affected by the AppArmor block seen on this host for the Kali VMs, see
        # unattended_install.py; busybox udhcpc is a different binary and path, not subject
        # to the same Debian AppArmor profile, but not explicitly verified here).
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
        # systemd-networkd (not ifupdown/dhclient, see DEBOOTSTRAP_INCLUDE above): built-in
        # DHCP, enabled explicitly (not enabled by default on Debian) through the same
        # symlink mechanism as ssh.service below.
        network_dir = rootfs / "etc" / "systemd" / "network"
        network_dir.mkdir(parents=True, exist_ok=True)
        (network_dir / "eth0.network").write_text("[Match]\nName=eth0\n\n[Network]\nDHCP=yes\n")

    # Static /etc/resolv.conf rather than the systemd-resolved stub (not enabled here,
    # no reason to add one more service for this first version): a public resolver,
    # enough for a container that needs outgoing network access (e.g. a manual
    # `apt install` once connected).
    (rootfs / "etc" / "resolv.conf").write_text("nameserver 1.1.1.1\nnameserver 9.9.9.9\n")

    # No -G sudo: the "sudo" group does not necessarily exist (Alpine does not create
    # it) and is not needed anyway, since sudo access is granted to this user by name
    # through sudoers.d below, not through group membership.
    subprocess.run(
        ["chroot", str(rootfs), "useradd", "-m", "-s", "/bin/bash", username],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["chroot", str(rootfs), "chpasswd", "-e"],
        input=f"{username}:{pwd_hash}\n",
        check=True,
        capture_output=True,
        text=True,
    )
    # Root locked, the same posture as the RHEL kickstart (rootpw --lock): only the
    # account created by name is usable.
    subprocess.run(["chroot", str(rootfs), "passwd", "-l", "root"], check=True, capture_output=True, text=True)

    # sudo WITHOUT a password for this account: membership of the "sudo" group alone
    # is not enough (the default Debian policy requires a password), seen in testing
    # (the web terminal could connect over SSH but `sudo` stayed blocked there). The
    # same posture as the Ubuntu autoinstall of the VMs
    # (`sudo: ALL=(ALL) NOPASSWD:ALL`, see
    # unattended_install.py::build_autoinstall_iso).
    sudoers_dropin = rootfs / "etc" / "sudoers.d" / "hyperlite-automation"
    sudoers_dropin.write_text(f"{username} ALL=(ALL) NOPASSWD:ALL\n")
    sudoers_dropin.chmod(0o440)

    uid = subprocess.run(
        ["chroot", str(rootfs), "id", "-u", username], check=True, capture_output=True, text=True
    ).stdout.strip()
    gid = subprocess.run(
        ["chroot", str(rootfs), "id", "-g", username], check=True, capture_output=True, text=True
    ).stdout.strip()

    ssh_dir = rootfs / "home" / username / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(ssh_pubkey + "\n")
    authorized_keys.chmod(0o600)
    subprocess.run(["chown", "-R", f"{uid}:{gid}", str(ssh_dir)], check=True)

    if family != "apk":
        # ssh.service is normally already enabled by the openssh-server package's postinst
        # (standard debootstrap --include behaviour); this direct symlink is an explicit
        # safety net rather than silently depending on that default behaviour.
        # systemd-networkd is NOT enabled by default on Debian (unlike ssh once the
        # package is installed), so the symlink is mandatory here, not just a safety net.
        # (Alpine/apk is already handled above through the OpenRC runlevels.)
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
    """Minimal LXC domain: /sbin/init (systemd) as PID 1, with the filesystem mounted
    directly from the rootfs on disk (no virtual/qcow2 disk as for VMs:
    containers share the host kernel, so there is no disk to emulate). The
    CPU/RAM limits are enforced natively by cgroups (<vcpu>/<memory>), with no
    need for the <cputune>/<memtune> XML used for QEMU VMs."""
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
