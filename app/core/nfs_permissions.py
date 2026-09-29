"""Can QEMU use the disks of an NFS pool? Checked once the pool is mounted, and explained when a VM cannot start.

QEMU does not run as root: libvirt runs it as the user of /etc/libvirt/qemu.conf (libvirt-qemu on Debian) and,
before a VM starts, hands each disk file over to that user. Most NFS exports use root_squash by default: the NFS
server treats this host's root as an anonymous user, so libvirt cannot change the owner of the disk files, QEMU
cannot open them, and the VM fails with "Permission denied" although the pool looks fine.

The check writes a small file on the share as root, tries to give it to QEMU's user, and looks at the owner the
server actually recorded. The advice names the export options that work: all_squash mapped to QEMU's ids (every
access through NFS becomes that user), or no_root_squash.
"""

import grp
import logging
import os
import pwd
import re
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

QEMU_CONF = Path("/etc/libvirt/qemu.conf")
# Where Hyperlite mounts the NFS pools it creates: the only directories check() writes into.
POOLS_ROOT = Path("/var/lib/libvirt/hyperlite-pools")
DEFAULT_USER = "libvirt-qemu"
DEFAULT_GROUP = "kvm"
_SETTING = re.compile(r'^\s*(user|group)\s*=\s*"([^"]+)"', re.MULTILINE)
_OPEN_DENIED = re.compile(r"Could not open '(/[^']+)': Permission denied")


def qemu_identity(conf=None):
    """(uid, gid, "user:group") QEMU runs as."""
    settings = {}
    try:
        settings = dict(_SETTING.findall(Path(conf or QEMU_CONF).read_text()))
    except OSError as e:
        logger.debug("qemu.conf unreadable, assuming libvirt's defaults: %s", e)
    user = settings.get("user", DEFAULT_USER)
    group = settings.get("group", DEFAULT_GROUP)

    def uid_of(name):
        return int(name.lstrip("+")) if name.lstrip("+").isdigit() else pwd.getpwnam(name).pw_uid

    def gid_of(name):
        return int(name.lstrip("+")) if name.lstrip("+").isdigit() else grp.getgrnam(name).gr_gid

    try:
        uid = uid_of(user)
    except KeyError:
        uid = 64055  # the fixed id of libvirt-qemu on Debian
    try:
        gid = gid_of(group)
    except KeyError:
        gid = uid
    return uid, gid, f"{user}:{group}"


def advice(uid, gid, export="/srv/share"):
    return (
        f"On the NFS server, export the share so that QEMU (uid {uid}) can use the disks, for example in "
        f"/etc/exports: '{export} <network>(rw,sync,no_subtree_check,all_squash,anonuid={uid},anongid={gid})' "
        "(or no_root_squash), then run 'exportfs -ra'. Existing disk files also need "
        f"'chown {uid}:{gid}' on the server. On a NAS, this is the NFS squash / user mapping option."
    )


def check(path, export="/srv/share"):
    """{"ok": bool, "message": str|None} for a mounted NFS pool directory."""
    # The service runs as root and writes a file here: only ever inside a pool mount point Hyperlite made (the
    # path comes from a pool name or from libvirt's configuration, never trusted as is). Resolved, so a
    # symbolic link cannot lead elsewhere.
    root = os.path.realpath(POOLS_ROOT)
    target = os.path.realpath(path)
    if not target.startswith(root + os.sep) or os.path.dirname(target) != root:
        return {
            "ok": False,
            "message": f"Only the NFS pools Hyperlite mounts under {POOLS_ROOT} can be checked; this one is at {path}.",
        }
    uid, gid, who = qemu_identity()
    probe = os.path.join(target, f".hyperlite-permission-check-{secrets.token_hex(4)}")
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        return {
            "ok": False,
            "message": f"This host cannot even write to the share ({e.strerror}). {advice(uid, gid, export)}",
        }
    try:
        os.close(fd)
        try:
            os.chown(probe, uid, gid)
        except OSError as e:  # judged by the owner the server recorded, below
            logger.debug("chown refused on the NFS share: %s", e)
        owner = os.stat(probe).st_uid
    finally:
        try:
            os.unlink(probe)
        except OSError as e:
            logger.warning("Could not remove the permission probe %s: %s", probe, e)
    if owner == uid:
        return {"ok": True, "message": None}
    return {
        "ok": False,
        "message": (
            f"The NFS share does not let this host give files to QEMU ({who}): files end up owned by uid {owner} "
            f"(the export probably uses root_squash), so VMs will fail to open their disks with 'Permission denied'. "
            f"{advice(uid, gid, export)}"
        ),
    }


def _nfs_mounts(mounts_file="/proc/mounts"):
    try:
        lines = Path(mounts_file).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and parts[2] in ("nfs", "nfs4"):
            out.append((parts[1].replace("\\040", " "), parts[0]))
    return out


def explain_denied_disk(message, mounts_file="/proc/mounts"):
    """For a QEMU "Could not open '<disk>': Permission denied" on an NFS mount, the reason and the fix; else None."""
    found = _OPEN_DENIED.search(message or "")
    if not found:
        return None
    disk = found.group(1)
    for mount_point, source in _nfs_mounts(mounts_file):
        if disk == mount_point or disk.startswith(mount_point.rstrip("/") + "/"):
            uid, gid, who = qemu_identity()
            export = source.split(":", 1)[1] if ":" in source else "/srv/share"
            return (
                f"The disk {disk} is on the NFS share {source}, which does not let QEMU ({who}) open it: the export "
                f"probably uses root_squash. {advice(uid, gid, export)}"
            )
    return None
