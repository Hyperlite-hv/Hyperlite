"""SMB/CIFS storage pools: a Windows server's or a NAS's share holding VM disks, as on Proxmox VE.

libvirt mounts the share itself (a "netfs" pool whose source format is "cifs"), so an SMB pool is shared storage
everywhere an NFS pool is: live migration, HA, a pool declared for every node. What it takes, checked against a
Samba server and the Linux 6.8 SMB client:

- **Credentials in a file.** libvirt always adds the `guest` option when it mounts a cifs pool, and mount.cifs then
  ignores a `password=` option ("password specified twice, ignoring second") or a `username=user%password`: the
  server refuses the session. A `credentials=` file is read in spite of `guest`. It lives on each node where the pool
  is mounted, root only (0600 in a 0700 directory), as Proxmox VE keeps `/etc/pve/priv/storage/<id>.pw`, and the
  pool's definition names it. The password is never in the pool's XML, nor on a command line, nor in /proc/mounts,
  and Hyperlite's API never returns it. A shared pool's password is also kept encrypted in Hyperlite's database
  (app/core/shared_pools.py), for the nodes registered later.
- **Every file owned by QEMU.** An SMB share has no Unix owners: `uid`, `gid`, `forceuid` and `forcegid` present every
  file as QEMU's user and group, with `file_mode=0660` and `dir_mode=0770`. libvirt's own chown of a disk then
  succeeds, and QEMU opens it. The user and group are given by name, resolved on each node by mount.
- **The SMB version**: negotiated by default (SMB 2.1 or later, 3.1.1 when the server offers it), or fixed for a
  server that negotiates badly.
"""

import logging
import os
import re
import secrets
import shlex
import subprocess
from pathlib import Path

from app.core import nfs_permissions, paths

logger = logging.getLogger(__name__)

VERSIONS = ("default", "3.1.1", "3.0", "2.1", "2.0")
# A share name as Windows and Samba accept it: no slash, no characters that mount or a URL would split on.
SHARE_RE = re.compile(r"^[A-Za-z0-9$][A-Za-z0-9 ._$-]{0,79}$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# The credentials file is one `key=value` per line: a value cannot hold a line break (or a NUL).
PASSWORD_FORBIDDEN = re.compile(r"[\r\n\x00]")
# On a node registered in the cluster: the same place as on this host in a standard installation.
REMOTE_DIR = "/etc/hyperlite/priv/smb"
SSH_TIMEOUT_S = 20


def credentials_dir(node=None):
    return Path(REMOTE_DIR) if node else paths.PRIV / "smb"


def credentials_path(pool_name, node=None):
    """Where a pool's credentials live on `node` (None: this host). The pool name is validated by the caller."""
    return str(credentials_dir(node) / f"{pool_name}.cred")


def validation_error(host_ok, share, user, password, domain, version):
    """A sentence for the first invalid field, or None."""
    if not host_ok:
        return "Invalid SMB server address"
    if not share or not SHARE_RE.match(share):
        return "Invalid share name (letters, digits, spaces, '.', '_', '-' and '$'; no slash)"
    if not user or not USER_RE.match(user):
        return "Invalid SMB user name"
    if not password or len(password) > 255 or PASSWORD_FORBIDDEN.search(password):
        return "The SMB password is required, at most 255 characters, on one line"
    if domain and not DOMAIN_RE.match(domain):
        return "Invalid SMB domain or workgroup"
    if version not in VERSIONS:
        return f"SMB version must be one of {', '.join(VERSIONS)}"
    return None


def credentials_text(user, password, domain=None):
    return f"username={user}\npassword={password}\n" + (f"domain={domain}\n" if domain else "")


def mount_options(cred_path, version="default"):
    """The mount options of the pool's definition, in order (libvirt adds its own first)."""
    _, _, who = nfs_permissions.qemu_identity()
    user, _, group = who.partition(":")
    options = [f"credentials={cred_path}"]
    if version != "default":
        options.append(f"vers={version}")
    return [
        *options,
        f"uid={user}",
        f"gid={group}",
        "forceuid",
        "forcegid",
        "file_mode=0660",
        "dir_mode=0770",
    ]


def _option(root, key):
    for option in root.iter("{http://libvirt.org/schemas/storagepool/fs/1.0}option"):
        name = option.get("name", "")
        if name.startswith(f"{key}="):
            return name.split("=", 1)[1]
    return None


def is_smb(root):
    """True for a pool definition (ElementTree root) that mounts an SMB share."""
    fmt = root.find("source/format")
    return root.get("type") == "netfs" and fmt is not None and fmt.get("type") == "cifs"


def version_of(root):
    return _option(root, "vers") or "default"


def credentials_of(root):
    """The credentials file a pool's definition names, if it is one Hyperlite writes (never another path)."""
    path = _option(root, "credentials")
    if not path:
        return None
    if os.path.dirname(path) not in (REMOTE_DIR, str(paths.PRIV / "smb")) or not path.endswith(".cred"):
        return None
    return path


def _node_ssh(node, command, input_text=None):
    from app.core import node_health
    from app.core.cluster import get_node, node_ssh_options

    target = get_node(node)
    if target is None:
        raise OSError(f"Node '{node}' not found")
    r = subprocess.run(
        [
            "ssh",
            *node_ssh_options(["-o", f"ConnectTimeout={node_health.CONNECT_TIMEOUT_S}"]),
            "-p",
            str(target["ssh_port"]),
            f"{target['ssh_user']}@{target['hostname']}",
            command,
        ],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT_S,
    )
    if r.returncode != 0:
        raise OSError(f"on node '{node}': {(r.stderr or r.stdout).strip() or f'exit status {r.returncode}'}")


def write_credentials(path, text, node=None):
    """Write the credentials file on `node` (None: this host), readable by root only. The password goes through the
    ssh connection's input, never on a command line."""
    if node:
        directory = os.path.dirname(path)
        _node_ssh(
            node,
            f"umask 077 && mkdir -p {shlex.quote(directory)} && chmod 700 {shlex.quote(directory)} "
            f"&& cat > {shlex.quote(path)}",
            input_text=text,
        )
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def remove_credentials(path, node=None):
    """Remove a credentials file. A failure is logged: the pool is gone either way."""
    try:
        if node:
            _node_ssh(node, f"rm -f {shlex.quote(path)}")
        else:
            Path(path).unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("SMB credentials %s not removed: %s", path, e)


def check(path):
    """{"ok", "message"}: can this host write a disk file on the mounted share, and is it QEMU's? A share mounted
    read-only, or whose SMB user may not write, mounts fine: VMs would then fail to create or write their disks."""
    uid, _, who = nfs_permissions.qemu_identity()
    probe = os.path.join(path, f".hyperlite-permission-check-{secrets.token_hex(4)}")
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        return {
            "ok": False,
            "message": f"This host cannot write to the SMB share ({e.strerror}): give the SMB user write access to it.",
        }
    try:
        os.close(fd)
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
        "message": f"Files on the SMB share are owned by uid {owner}, not by QEMU ({who}): VMs may fail to open "
        "their disks. Remove the pool and create it again from Hyperlite, which mounts it with QEMU's ids.",
    }
