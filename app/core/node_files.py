"""A node's own files and the cluster's tree (docs/design/cfs-files.md, sections 5 and 8): where each file of
/etc/hyperlite lives in the tree, and the bridge's restore, which puts them back on disk.

Step 3 moves the node's files into hyperlite-cfs's tree, under nodes/<host>/, and mounts the tree at /etc/hyperlite. The
version that runs before it (the bridge, this one) moves nothing, but finds them there: a rollback from step 3
reinstalls it, and its postinst calls restore() before anything reads /etc/hyperlite (the node's keys, priv/env, tell a
fresh installation from an upgrade: without them it would make new ones). Run as root with the system's python3 (no
venv yet in the postinst, `cd /usr/lib/hyperlite && python3 -m app.core.node_files restore`): this module and
cfs_client use the standard library only.

  /etc/hyperlite/local/<name>     nodes/<host>/<name>            the TLS certificate and key, the VLAN-aware bridges
  /etc/hyperlite/priv/<path>      nodes/<host>/priv/<path>       the node's keys, its SSH keys, SMB credentials
  /etc/hyperlite/authkeys.json    nodes/<host>/authkeys.json     the public keys sessions are verified with
  /etc/hyperlite/access.db        /var/lib/hyperlite/access/     a database, never in the tree (root's directory)

A file already on disk is never replaced: the tree's copy fills what is missing, then the directory step 3 moved aside
(/var/lib/hyperlite/etc-before-cfs-<date>, the newest) fills what the tree lacks. /var/lib/hyperlite is writable by the
API's group: a kept directory or a database there is trusted only when root's and closed to others, never one the API
could have made. SMB pools whose credentials step 3 read from /etc/hyperlite/local/priv/smb read them from
/etc/hyperlite/priv/smb again."""

import argparse
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from app.core import cfs_client, paths

# Files of /etc/hyperlite itself (not under local/ or priv/) that are the node's.
TOP = ("authkeys.json",)
ACCESS_DB = "access.db"
ACCESS_DIR = "access"  # under /var/lib/hyperlite: root's, as /etc/hyperlite was
KEPT_PREFIX = "etc-before-cfs-"


def host():
    """This node's name in the tree: the host name, as hyperlite-cfs names `local` (cfs/src/mount.c)."""
    return socket.gethostname()


def tree_path(rel, node=None):
    """The tree's path of `rel`, a file's path under /etc/hyperlite ("local/hyperlite.crt"); None for a file that is
    not the node's (access.db, the mount point)."""
    parts = PurePosixPath(rel).parts
    if not parts or ".." in parts:
        return None
    base = f"/nodes/{node or host()}"
    if parts[0] == "local" and len(parts) > 1:
        return base + "/" + "/".join(parts[1:])
    if parts[0] == "priv" and len(parts) > 1:
        return base + "/priv/" + "/".join(parts[1:])
    if len(parts) == 1 and parts[0] in TOP:
        return base + "/" + parts[0]
    return None


def disk_path(tree_rel):
    """The path under /etc/hyperlite of `tree_rel`, a path under nodes/<host>/ ("priv/env"): tree_path's inverse."""
    parts = PurePosixPath(tree_rel).parts
    if not parts or ".." in parts:
        return None
    if parts[0] == "priv":
        return "/".join(parts) if len(parts) > 1 else None
    if len(parts) == 1 and parts[0] in TOP:
        return parts[0]
    return "local/" + "/".join(parts)


def from_tree(client, node=None):
    """{path under /etc/hyperlite: bytes} for every file of this node in the tree; {} when it holds none."""
    base = f"/nodes/{node or host()}"
    found = {}

    def walk(rel):
        try:
            children = client.list(base + ("/" + rel if rel else ""))
        except cfs_client.NotFound:
            return
        for child in children:
            sub = f"{rel}/{child.name}" if rel else child.name
            if child.is_dir:
                walk(sub)
                continue
            target = disk_path(sub)
            if target:
                found[target] = client.get(f"{base}/{sub}").data

    walk("")
    return found


def _roots(path):
    """Root's (the restore's own account, root's on a node), and writable by no one else: a path the API's group could
    not have made or changed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return st.st_uid in (0, os.geteuid()) and not st.st_mode & 0o022


def kept_dir(var):
    """The newest directory step 3 moved /etc/hyperlite to, or None. Only root's."""
    kept = sorted(p for p in Path(var).glob(KEPT_PREFIX + "*") if p.is_dir() and not p.is_symlink() and _roots(p))
    return kept[-1] if kept else None


def _mode(rel):
    """As the installation makes them: root's alone under priv/, a key root's and the API group's (which the
    postinst's systemd-tmpfiles gives it, installer/hyperlite.tmpfiles), the rest public (a certificate, public keys)."""
    if rel.startswith("priv/"):
        return 0o600
    return 0o640 if rel.endswith(".key") else 0o644


def _write(etc, rel, data, mode=None):
    dest = Path(etc) / rel
    if dest.exists() or dest.is_symlink():
        return False
    for parent in reversed(dest.relative_to(etc).parents[:-1]):
        d = Path(etc) / parent
        if not d.exists():
            d.mkdir(mode=0o700 if str(parent).startswith("priv") else 0o750)
    mode = _mode(rel) if mode is None else mode
    tmp = dest.with_name(f".{dest.name}.restore")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, dest)
    return True


def _virsh(*args, data=None):
    try:
        return subprocess.run(["virsh", *args], input=data, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def smb_pools(old, new, virsh=_virsh):
    """Point every libvirt SMB pool whose credentials are in directory `old` at `new` (their definition names the
    file). The names of the pools changed."""
    changed = []
    listed = virsh("pool-list", "--all", "--name")
    for name in [n.strip() for n in listed.stdout.splitlines() if n.strip()] if listed.returncode == 0 else []:
        xml = virsh("pool-dumpxml", "--inactive", name)
        if xml.returncode or f"credentials={old}/" not in xml.stdout:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".xml") as f:
            f.write(xml.stdout.replace(f"credentials={old}/", f"credentials={new}/"))
            f.flush()
            if virsh("pool-define", f.name).returncode == 0:
                changed.append(name)
    return changed


def _is_mount(path):
    return subprocess.run(["mountpoint", "-q", str(path)], capture_output=True).returncode == 0


def _unmount(path):
    """The tree mounted over /etc/hyperlite (step 3): stopped first, so the files go to the disk beneath."""
    subprocess.run(["systemctl", "stop", "hyperlite-cfs.service"], capture_output=True)
    subprocess.run(["fusermount3", "-u", "-z", str(path)], capture_output=True)


def restore(etc=None, var=None, client=None, node=None, is_mount=_is_mount, unmount=_unmount, virsh=None, log=print):
    """Put the node's files back in /etc/hyperlite from the tree, then from the directory step 3 kept, and access.db
    back from /var/lib/hyperlite. Never replaces a file on disk. Returns the paths written."""
    etc, var = Path(etc or paths.ETC), Path(var or paths.VAR)
    virsh = virsh or _virsh
    tree = {}
    if client is not None:
        try:
            tree = from_tree(client, node)
        except (OSError, cfs_client.CfsError) as e:
            log(f"the tree could not be read ({e}): the node's files are looked for in the kept directory only")
    if is_mount(etc):
        unmount(etc)
        if is_mount(etc):
            raise RuntimeError(f"{etc} is still mounted: its files cannot be put back")
    etc.mkdir(mode=0o755, exist_ok=True)
    written = []
    for rel, data in sorted(tree.items()):
        if _write(etc, rel, data):
            written.append(rel)
    kept = kept_dir(var)
    if kept:
        for src in sorted(kept.rglob("*")):
            rel = src.relative_to(kept).as_posix()
            if rel.startswith(("cfs/", ACCESS_DB)) or not src.is_file() or src.is_symlink():
                continue
            if _write(etc, rel, src.read_bytes(), src.stat().st_mode & 0o7777):
                written.append(rel)
    # access.db: where step 3 moved it, else as it was kept. The database's own files, never one of them alone.
    if not (etc / ACCESS_DB).exists():
        moved = var / ACCESS_DIR
        for source in (moved if _roots(moved) else None, kept):
            if source and (source / ACCESS_DB).exists() and _roots(source / ACCESS_DB):
                for suffix in ("", "-wal", "-shm"):
                    if (source / (ACCESS_DB + suffix)).exists():
                        move = shutil.move if source == moved else shutil.copy2
                        move(source / (ACCESS_DB + suffix), etc / (ACCESS_DB + suffix))
                written.append(ACCESS_DB)
                break
    for rel in written:
        log(f"put back: {etc / rel}")
    for name in smb_pools(str(etc / "local" / "priv" / "smb"), str(etc / "priv" / "smb"), virsh):
        log(f"SMB pool {name}: its credentials are read from {etc / 'priv' / 'smb'} again")
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["restore"])
    parser.add_argument("--socket", default=cfs_client.DEFAULT_SOCKET)
    args = parser.parse_args(argv)
    client = None
    if os.path.exists(args.socket):
        client = cfs_client.CfsClient(args.socket, timeout=10.0)
    try:
        restore(client=client, log=lambda m: print(f"[hyperlite-node-files] {m}"))
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
