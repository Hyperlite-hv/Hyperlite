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
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.core import api_group, cfs_client, paths

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


# ---- Step 3: the node's files in the tree (section 15) ----

_client_factory = None  # replaced in the tests


def _client():
    if _client_factory:
        return _client_factory()
    return cfs_client.CfsClient(cfs_client.DEFAULT_SOCKET, timeout=10.0)


def in_tree(etc=None):
    """Whether /etc/hyperlite is the tree's mount: only the mount shows .version."""
    return (Path(etc or paths.ETC) / ".version").is_file()


def relative(path, etc=None):
    """A node's file as its path under /etc/hyperlite on disk ("priv/env"), from any path it is read at: on disk
    (/etc/hyperlite/priv/env) or in the tree (/etc/hyperlite/local/priv/env). None for any other path."""
    etc = Path(etc or paths.ETC)
    try:
        rel = PurePosixPath(os.path.normpath(path)).relative_to(etc)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) > 2 and parts[:2] == ("local", "priv"):
        rel = PurePosixPath("priv", *parts[2:])
    elif parts == ("local", "authkeys.json"):
        rel = PurePosixPath("authkeys.json")
    return str(rel) if tree_path(str(rel), "-") else None


def location(rel, etc=None, tree=None):
    """Where `rel` ("priv/env") is read: /etc/hyperlite/<rel> on disk, /etc/hyperlite/local/<its path under
    nodes/<host>/> in the tree."""
    etc = Path(etc or paths.ETC)
    if not (in_tree(etc) if tree is None else tree):
        return etc / rel
    return etc / "local" / tree_path(rel, "-")[len("/nodes/-/") :]


def write(path, data, mode=None):
    """Write a node's file, wherever it lives: in the tree through the socket (root), or on disk atomically with
    `mode` (the installation's by default). `path` is any path the file is read at."""
    rel = relative(path)
    if rel is None:
        raise ValueError(f"{path} is not one of the node's files in {paths.ETC}")
    data = data.encode() if isinstance(data, str) else bytes(data)
    if in_tree():
        with _client() as c:
            c.put(tree_path(rel), data)
    else:
        _write(paths.ETC, rel, data, mode, replace=True)


def is_node_file(path):
    """`path` is one of the node's files and lives in the tree: written with write(), never on the mount."""
    return relative(path) is not None and in_tree()


def update(path, change, mode=None, attempts=20):
    """Read, change and write a node's file in the tree without losing another writer's change (compare-and-set on
    the entry's version): `change(old bytes or None)` returns the new bytes, or None to leave it. Returns what is
    there after."""
    tp = tree_path(relative(path))
    with _client() as c:
        for _ in range(attempts):
            try:
                entry = c.get(tp)
                old, expected = entry.data, entry.version
            except cfs_client.NotFound:
                old, expected = None, cfs_client.MUST_NOT_EXIST
            new = change(old)
            if new is None:
                return old
            try:
                c.put(tp, new, expected)
                return new
            except cfs_client.Conflict:
                continue
    raise RuntimeError(f"{path}: changed by another writer {attempts} times in a row")


def put_all(files, node=None, client=None, wait_s=60):
    """{rel: bytes} written as this node's files in the tree, waiting for a tree still synchronising (a join)."""
    node = node or host()
    c = client or _client()
    deadline = time.monotonic() + wait_s
    try:
        for rel, data in sorted(files.items()):
            while True:
                try:
                    c.put(tree_path(rel, node), data)
                    break
                except (cfs_client.Synchronising, cfs_client.ReadOnly):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.5)
    finally:
        if client is None:
            c.close()


def drop_node(name, client=None):
    """Every file of node `name` out of the tree (a member removed). The tree names a node by its host name, the
    cluster by its node name (the host name, lowercase and without its domain): both are matched."""
    c = client or _client()
    try:
        try:
            hosts = [ch.name for ch in c.list("/nodes") if ch.is_dir and ch.name.lower().split(".")[0] == name]
        except cfs_client.NotFound:
            hosts = []
        for node in hosts:
            for rel in from_tree(c, node):
                with contextlib.suppress(cfs_client.NotFound):
                    c.delete(tree_path(rel, node))
    finally:
        if client is None:
            c.close()


def remove(path):
    rel = relative(path)
    if rel is None:
        raise ValueError(f"{path} is not one of the node's files in {paths.ETC}")
    if in_tree():
        with _client() as c, contextlib.suppress(cfs_client.NotFound):
            c.delete(tree_path(rel))
    else:
        (paths.ETC / rel).unlink(missing_ok=True)


def keygen(path, comment):
    """An Ed25519 key pair at `path` and `path`.pub, made in a directory of its own then written as the node's
    files: ssh-keygen cannot write into the tree."""
    with tempfile.TemporaryDirectory(prefix="hyperlite-keygen-") as tmp:
        key = Path(tmp) / "key"
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", comment],
            check=True,
            capture_output=True,
            text=True,
        )
        write(path, key.read_bytes(), 0o600)
        write(f"{path}.pub", key.with_suffix(".pub").read_bytes(), 0o644)


def on_disk(etc=None):
    """{rel: bytes} for every node's file on disk under /etc/hyperlite, and the paths left out (not a node's file).
    cfs/, where the tree may be mounted, is never walked."""
    etc = Path(etc or paths.ETC)
    found, other = {}, []
    for root, dirs, names in os.walk(etc):
        here = Path(root)
        if here == etc:
            dirs[:] = [d for d in dirs if d != "cfs"]
        dirs.sort()
        for name in sorted(names):
            src = here / name
            rel = src.relative_to(etc).as_posix()
            if here == etc and name.startswith(ACCESS_DB):
                continue
            if src.is_file() and not src.is_symlink() and tree_path(rel, "-"):
                found[rel] = src.read_bytes()
            else:
                other.append(rel)
    return found, other


def _systemctl(*args):
    return subprocess.run(["systemctl", *args], capture_output=True, text=True).returncode == 0


def _wait(check, timeout_s):
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def move(etc=None, var=None, client=None, node=None, systemctl=_systemctl, virsh=_virsh, log=print, wait_s=30):
    """Step 3, once: the node's files of /etc/hyperlite into the tree, access.db to /var/lib/hyperlite, the directory
    moved aside and the tree mounted in its place (section 15). Nothing when already done. Run with the API stopped
    (hyperlite.service's ExecStartPre); stops and starts hyperlite-daemon and hyperlite-cfs itself."""
    etc, var = Path(etc or paths.ETC), Path(var or paths.VAR)
    if in_tree(etc):
        return []
    files, other = on_disk(etc)
    if not files and kept_dir(var):
        raise RuntimeError(
            f"{etc} is empty: hyperlite-cfs has not mounted the tree there (systemctl status hyperlite-cfs)"
        )
    if "priv/env" not in files:
        raise RuntimeError(f"{etc}/priv/env is missing: the node's keys cannot be moved")
    for rel in other:
        log(f"not one of the node's files, left in the kept directory: {rel}")
    client = client or _client()
    node = node or host()
    systemctl("stop", "hyperlite-daemon.service")
    try:
        base = f"/nodes/{node}"
        stale = set(from_tree(client, node)) - set(files)  # left by an earlier move: the disk is the newer
        for rel, data in files.items():
            client.put(tree_path(rel, node), data)
            if client.get(tree_path(rel, node)).data != data:
                raise RuntimeError(f"{rel} does not read back the same from the tree")
        for rel in stale:
            client.delete(tree_path(rel, node))
        log(f"{len(files)} files in the tree under {base}")
    except Exception:
        systemctl("start", "hyperlite-daemon.service")
        raise
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = var / f"{KEPT_PREFIX}{stamp}"
    old_smb, new_smb = str(etc / "priv" / "smb"), str(etc / "local" / "priv" / "smb")
    moved_db = []
    try:
        access = var / ACCESS_DIR
        access.mkdir(mode=0o700, exist_ok=True)
        api_group.share(access, 0o750)  # the API reads the permissions, never writes them
        for suffix in ("", "-wal", "-shm"):
            if (etc / (ACCESS_DB + suffix)).exists():
                shutil.move(etc / (ACCESS_DB + suffix), access / (ACCESS_DB + suffix))
                moved_db.append(suffix)
        pools = smb_pools(old_smb, new_smb, virsh)
        systemctl("stop", "hyperlite-cfs.service")
        subprocess.run(["fusermount3", "-u", "-z", str(etc / "cfs")], capture_output=True)
        shutil.move(etc, aside)
        os.chmod(aside, 0o700)  # the node's keys, in a directory the API's group may list
        etc.mkdir(mode=0o755)
    except Exception:
        _undo_move(etc, var, aside, moved_db, old_smb, new_smb, systemctl, virsh)
        raise
    systemctl("start", "hyperlite-cfs.service")
    if not _wait(lambda: in_tree(etc), wait_s):
        log("the tree did not mount at /etc/hyperlite: the files go back where they were")
        systemctl("stop", "hyperlite-cfs.service")
        subprocess.run(["fusermount3", "-u", "-z", str(etc)], capture_output=True)
        _undo_move(etc, var, aside, moved_db, old_smb, new_smb, systemctl, virsh)
        raise RuntimeError("hyperlite-cfs did not mount the tree at /etc/hyperlite: see journalctl -u hyperlite-cfs")
    systemctl("start", "hyperlite-daemon.service")
    log(f"moved: {etc} is the tree; the files as they were are kept in {aside}")
    for name in pools:
        log(f"SMB pool {name}: its credentials are read from {new_smb}")
    return sorted(files)


def _undo_move(etc, var, aside, moved_db, old_smb, new_smb, systemctl, virsh):
    if aside.exists():
        if etc.exists() and any(etc.iterdir()):
            shutil.rmtree(aside)  # a copy to another file system cut short: the files are still where they were
        else:
            os.chmod(aside, 0o755)  # noqa: S103 - /etc/hyperlite's own mode, back
            if etc.exists():
                etc.rmdir()
            shutil.move(aside, etc)
    for suffix in moved_db:
        shutil.move(var / ACCESS_DIR / (ACCESS_DB + suffix), etc / (ACCESS_DB + suffix))
    smb_pools(new_smb, old_smb, virsh)
    systemctl("start", "hyperlite-cfs.service")
    systemctl("start", "hyperlite-daemon.service")


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
    parser.add_argument("action", choices=["restore", "move", "put", "remove", "path"])
    parser.add_argument("file", nargs="?", help="put, path: the file, as its path under /etc/hyperlite on disk")
    parser.add_argument("--mode", type=lambda v: int(v, 8), help="put: its mode on disk (octal)")
    parser.add_argument("--socket", default=cfs_client.DEFAULT_SOCKET)
    args = parser.parse_args(argv)
    say = lambda m: print(f"[hyperlite-node-files] {m}")  # noqa: E731
    if args.action == "path":
        print(location(args.file))
        return
    if args.action in ("put", "remove") and relative(paths.ETC / (args.file or "")) is None:
        parser.error(f"{args.file}: not one of the node's files (local/..., priv/..., authkeys.json)")
    if args.action == "put":
        write(paths.ETC / args.file, sys.stdin.buffer.read(), args.mode)
        return
    if args.action == "remove":
        remove(paths.ETC / args.file)
        return
    if args.action == "move":
        move(log=say)
        return
    client = None
    if os.path.exists(args.socket):
        client = cfs_client.CfsClient(args.socket, timeout=10.0)
    try:
        restore(client=client, log=say)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
