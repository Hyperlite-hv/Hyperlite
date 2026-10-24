"""Container snapshots (docs/design/container-snapshots.md): copies of a stopped container's directory, since libvirt's
LXC driver has none and Hyperlite's containers live in plain directories.

A snapshot is `<container's parent directory>/.snapshots/<container>/<snapshot>/` (root only): `rootfs/` and
`snapshot.json`. Each new snapshot hard-links the files the previous one already holds unchanged (rsync --link-dest),
so only what changed takes room. --checksum compares contents: rsync's default quick check (size and mtime) linked a
file whose content had changed with neither, and the snapshot kept the old content (checked).

A restore copies, never links: the container's files and the snapshot's must not be the same inodes, or the container
would write into its snapshot.
"""

import json
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.core.container_builder import container_rootfs_path
from app.core.safe_paths import safe_child

SNAPSHOTS = ".snapshots"
RSYNC = ["rsync", "-aHAXS", "--numeric-ids"]
# A full copy needs room for the container's whole file system; this much more is kept free on the host.
MARGIN_BYTES = 512 * 1024 * 1024


class SnapshotError(Exception):
    pass


class ContainerBusy(SnapshotError):
    def __init__(self, name, running):
        super().__init__(f"Container '{name}' is busy: {running} is in progress. Try again when it has finished.")


_lock = threading.Lock()
_busy = {}  # container name -> what runs on it


class Claim:
    """One operation at a time on a container's files, released once (release() or leaving a `with` block). Taken by
    the endpoint, so the refusal reaches the caller; released by the thread that does the work."""

    def __init__(self, name):
        self.name, self._released = name, False

    def release(self):
        with _lock:
            if not self._released:
                self._released = True
                _busy.pop(self.name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()
        return False


def claim(name, what):
    """Raises ContainerBusy when another operation runs on the container."""
    with _lock:
        if name in _busy:
            raise ContainerBusy(name, _busy[name])
        _busy[name] = what
    return Claim(name)


def busy(name):
    """What runs on the container's files, or None."""
    with _lock:
        return _busy.get(name)


def _snapshots_dir(name):
    return safe_child(safe_child(container_rootfs_path(name).parent, SNAPSHOTS), name)


def _snapshot_dir(name, snapshot):
    return safe_child(_snapshots_dir(name), snapshot)


def _read(path):
    """A complete snapshot's description, or None: a `.partial` copy (a service stopped in the middle of one) has its
    description written before it takes its final name, and is never one."""
    try:
        meta = json.loads((path / "snapshot.json").read_text())
    except (OSError, ValueError):
        return None
    return meta if meta.get("name") == path.name and (path / "rootfs").is_dir() else None


def list_snapshots(name):
    """The container's complete snapshots, oldest first: name, description, created_at."""
    directory = _snapshots_dir(name)
    if not directory.is_dir():
        return []
    found = [meta for entry in directory.iterdir() if entry.is_dir() and (meta := _read(entry))]
    return sorted(found, key=lambda m: (m["created_at"], m["name"]))


def exists(name, snapshot):
    return _read(_snapshot_dir(name, snapshot)) is not None


def _disk_usage(path):
    """Bytes the tree takes on disk (each inode once)."""
    seen, total = set(), 0
    for dirpath, dirnames, filenames in os.walk(path):
        for entry in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, entry))
            if st.st_ino not in seen:
                seen.add(st.st_ino)
                total += st.st_blocks * 512
    return total


def _require_room(source, where):
    need = _disk_usage(source) + MARGIN_BYTES
    free = shutil.disk_usage(where).free
    if free < need:
        raise SnapshotError(
            f"Not enough free space for a full copy of the container: {need // 2**20} MiB needed, "
            f"{free // 2**20} MiB free in {where}"
        )


def _rsync(*args):
    r = subprocess.run([*RSYNC, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SnapshotError(f"rsync failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-500:]}")


def create(name, snapshot, description=None):
    """Snapshot of the stopped container (the caller checks it is stopped and holds its claim)."""
    rootfs = container_rootfs_path(name)
    if not rootfs.is_dir():
        raise SnapshotError(f"Filesystem of container '{name}' not found")
    final = _snapshot_dir(name, snapshot)
    if os.path.lexists(final):
        raise SnapshotError(f"A snapshot '{snapshot}' already exists for this container")
    previous = list_snapshots(name)
    directory = _snapshots_dir(name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory.parent, 0o700)
    if not previous:
        _require_room(rootfs, directory)
    partial = safe_child(directory, f"{snapshot}.partial")
    shutil.rmtree(partial, ignore_errors=True)  # left by a service stopped in the middle of one
    try:
        partial.mkdir(mode=0o700)
        link = ["--checksum", f"--link-dest={_snapshot_dir(name, previous[-1]['name']) / 'rootfs'}"] if previous else []
        _rsync(*link, f"{rootfs}/", f"{partial / 'rootfs'}/")
        meta = {"name": snapshot, "description": description, "created_at": datetime.now(UTC).isoformat()}
        (partial / "snapshot.json").write_text(json.dumps(meta))
        os.rename(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return meta


def restore(name, snapshot):
    """The stopped container's file system becomes the snapshot's (the caller checks it is stopped and holds its
    claim). A failure before the swap leaves it as it was."""
    source = _snapshot_dir(name, snapshot)
    if _read(source) is None:
        raise SnapshotError(f"Snapshot '{snapshot}' not found")
    rootfs = container_rootfs_path(name)
    restoring = safe_child(rootfs.parent, f"{rootfs.name}.restoring")
    old = safe_child(rootfs.parent, f"{rootfs.name}.replaced")
    for leftover in (restoring, old):
        shutil.rmtree(leftover, ignore_errors=True)
    _require_room(source / "rootfs", rootfs.parent)
    try:
        _rsync(f"{source / 'rootfs'}/", f"{restoring}/")
    except BaseException:
        shutil.rmtree(restoring, ignore_errors=True)
        raise
    os.rename(rootfs, old)
    try:
        os.rename(restoring, rootfs)
    except BaseException:
        os.rename(old, rootfs)
        shutil.rmtree(restoring, ignore_errors=True)
        raise
    shutil.rmtree(old)


def delete(name, snapshot):
    path = _snapshot_dir(name, snapshot)
    if _read(path) is None:
        raise SnapshotError(f"Snapshot '{snapshot}' not found")
    shutil.rmtree(path)


def delete_all(name, parent):
    """With the container (its directory under `parent`): its snapshots go too."""
    shutil.rmtree(safe_child(safe_child(Path(parent), SNAPSHOTS), name), ignore_errors=True)


def rename_all(old, new, parent):
    """With a renamed container (its directory under `parent`): its snapshots follow."""
    base = safe_child(Path(parent), SNAPSHOTS)
    src, dst = safe_child(base, old), safe_child(base, new)
    if src.is_dir():
        if os.path.lexists(dst):
            raise SnapshotError(f"Snapshots named after '{new}' are already on disk: {dst}")
        os.rename(src, dst)
