"""ZFS storage on raw zvols. zvols were chosen over qcow2-on-dataset so the
same mechanism can be reused later with Ceph/RBD: both present themselves to a
VM as a raw BLOCK device on the host, and only the source of the device path
changes.

Why not libvirt's storage pool API (like dir/netfs, app/routers/storage.py):
it was checked on the host (/usr/lib/x86_64-linux-gnu/libvirt/storage-backend/)
that no 'zfs' driver is compiled into the installed libvirt package. ZFS is
therefore managed HERE through direct `zpool`/`zfs` subprocess calls, never
through virStoragePool/virStorageVol. The separate package
libvirt-daemon-driver-storage-zfs exists, but its volume creation support is
historically limited (it only lists PRE-EXISTING zvols). It is not used: the
whole lifecycle (pool AND zvol) stays under Hyperlite's direct control, with no
dependency on that driver.

There is no dedicated SQLite table: the real state lives entirely in ZFS itself
(zpool list / zfs list), queried on every call. Same philosophy as
app/routers/storage.py, which only trusts libvirt's real state and never a
database mirror that could diverge.

Pools can be backed by loopback FILES (`zpool create <name> <file>`: ZFS natively
accepts a regular file as a vdev, no explicit `losetup` needed), which avoids
touching an existing LVM or requiring a dedicated disk to try the mechanism.
This is transparent for the rest of the code: replacing the file path with a
real block device (`/dev/sdX`) when moving to a real disk changes nothing else.

"""

import re
import subprocess
import time
from pathlib import Path

LOOPBACK_DIR = Path("/var/lib/hyperlite-zfs")

# ZFS names: the same constraints as VM/pool names elsewhere in this project
# (letters/digits/dashes), and never raw user text injected as is into a
# zfs/zpool command.
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}$")


class ZfsError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ZfsNotFoundError(ZfsError):
    """The requested pool, zvol or snapshot does not exist."""


def validate_zfs_name(name):
    if not NAME_RE.match(name):
        return (
            "Invalid ZFS name (letters/digits/dashes/underscores, 2-63 characters, must start with a letter or a digit)"
        )
    return None


def _run(*args, check=True):
    # Guard against the zpool/zfs binaries being absent: without it, a raw
    # FileNotFoundError propagated all the way up to GET /storage, BREAKING THE WHOLE
    # ENDPOINT (not just the ZFS part) with a 500 on a machine that simply does not
    # have ZFS installed. is_available() existed but was never wired into this shared
    # entry point.
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True)
    except FileNotFoundError:
        if check:
            raise ZfsError("ZFS is not installed on this host (zfs/zpool binary not found)") from None
        return subprocess.CompletedProcess(args, 127, "", "zfs/zpool not found")
    if check and proc.returncode != 0:
        raise ZfsError((proc.stderr or proc.stdout or f"Command failed: {' '.join(args)}").strip())
    return proc


def is_available():
    """True if the zfs/zpool binaries are installed on this host. Checked before
    exposing anything on the API side, rather than letting a raw
    FileNotFoundError propagate (the same principle as the missing `git` check)."""
    from shutil import which

    return which("zpool") is not None and which("zfs") is not None


def _backing_file_of(pool_name):
    """Path of the loopback file that serves as this pool's vdev, found by parsing
    `zpool status`: ZFS stores this information nowhere else in a structured,
    easily queried form. Returns None if it cannot be found (a pool on a real
    disk, or an unexpected output format). Never blocking: it only means one
    best-effort file removal fewer when destroying the pool."""
    proc = _run("zpool", "status", "-P", pool_name, check=False)
    if proc.returncode != 0:
        return None
    in_config = False
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == "config:":
            in_config = True
            continue
        if not in_config or not stripped or stripped.startswith("NAME "):
            continue
        first_token = stripped.split()[0]
        if first_token == pool_name:
            continue  # the pool's own line, not a vdev
        if first_token.startswith("/"):
            return first_token
        if stripped.startswith(("errors:", "mirror", "raidz")):
            continue
    return None


def list_pools():
    """List of the ZFS pools managed by this host, with the same fields as
    _pool_summary() in app/routers/storage.py (capacity/allocation/available in
    GB) so they merge easily into the unified list of the Storage tab."""
    proc = _run("zpool", "list", "-H", "-p", "-o", "name,size,alloc,free,health", check=False)
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        name, size, alloc, free, health = parts
        result.append(
            {
                "nom": name,
                "type": "zfs",
                "etat": "actif" if health == "ONLINE" else health.lower(),
                "capacite_go": round(int(size) / (1024**3), 2),
                "allocation_go": round(int(alloc) / (1024**3), 2),
                "disponible_go": round(int(free) / (1024**3), 2),
            }
        )
    return result


def pool_exists(name):
    return _run("zpool", "list", "-H", name, check=False).returncode == 0


def create_pool(name, size_gb):
    """Create a ZFS pool backed by a loopback file of `size_gb` GB in
    LOOPBACK_DIR. `compression=lz4` (nearly free in CPU, a real gain on most
    system disks) and `mountpoint=none` (this pool ONLY serves as a container
    for zvols, never a file dataset mounted somewhere, so no reason to risk a
    mount point collision)."""
    name_error = validate_zfs_name(name)
    if name_error:
        raise ZfsError(name_error)
    if pool_exists(name):
        raise ZfsError(f"A ZFS pool '{name}' already exists")

    LOOPBACK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    backing_file = LOOPBACK_DIR / f"{name}.img"
    if backing_file.exists():
        raise ZfsError(f"The backing file '{backing_file}' already exists (pool deleted without a complete cleanup?)")

    _run("truncate", "-s", f"{size_gb}G", str(backing_file))
    try:
        _run("zpool", "create", "-O", "compression=lz4", "-O", "mountpoint=none", name, str(backing_file))
    except ZfsError:
        backing_file.unlink(missing_ok=True)
        raise
    return list_pool(name)


def list_pool(name):
    for pool in list_pools():
        if pool["nom"] == name:
            return pool
    raise ZfsNotFoundError(f"ZFS pool '{name}' not found")


def delete_pool(name):
    if not pool_exists(name):
        raise ZfsNotFoundError(f"ZFS pool '{name}' not found")
    zvols = list_zvols(name)
    if zvols:
        raise ZfsError(f"Pool '{name}' still contains {len(zvols)} zvol(s), delete them first")

    backing_file = _backing_file_of(name)
    _run("zpool", "destroy", name)
    if backing_file:
        Path(backing_file).unlink(missing_ok=True)


def list_zvols(pool_name):
    proc = _run("zfs", "list", "-t", "volume", "-H", "-p", "-o", "name,volsize,used", "-r", pool_name, check=False)
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        full_name, volsize, used = parts
        # full_name = "<pool>/<zvol>": only the zvol part is kept, the pool is already
        # known to the caller.
        zvol_name = full_name.split("/", 1)[1] if "/" in full_name else full_name
        result.append(
            {
                "nom": zvol_name,
                "chemin": device_path(pool_name, zvol_name),
                "capacite_go": round(int(volsize) / (1024**3), 3),
                "allocation_go": round(int(used) / (1024**3), 3),
            }
        )
    return result


def device_path(pool_name, zvol_name):
    return f"/dev/zvol/{pool_name}/{zvol_name}"


def _wait_for_device(path, timeout_s=5):
    """udev takes a moment to create the device node after `zfs create -V`; this is
    expected and documented (unlike a regular qcow2 file, which is available
    immediately). Blocks up to `timeout_s`, never longer: better to fail clearly
    afterwards than to hand the caller a path that does not exist yet."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.1)
    return Path(path).exists()


def create_zvol(pool_name, zvol_name, size_gb):
    """Create a zvol of `size_gb` GB, sparse (thin-provisioned, `-s`), the same
    principle as the existing qcow2 disks (no immediate reservation of all the
    space), then wait for the device node to really exist before returning."""
    name_error = validate_zfs_name(zvol_name)
    if name_error:
        raise ZfsError(name_error)
    full_name = f"{pool_name}/{zvol_name}"
    if _run("zfs", "list", "-H", full_name, check=False).returncode == 0:
        raise ZfsError(f"A zvol '{zvol_name}' already exists in pool '{pool_name}'")

    _run("zfs", "create", "-s", "-V", f"{size_gb}G", full_name)
    path = device_path(pool_name, zvol_name)
    if not _wait_for_device(path):
        raise ZfsError(f"Zvol '{full_name}' created but device '{path}' never appeared (udev)")
    return path


def delete_zvol(pool_name, zvol_name):
    full_name = f"{pool_name}/{zvol_name}"
    if _run("zfs", "list", "-H", full_name, check=False).returncode != 0:
        raise ZfsNotFoundError(f"Zvol '{zvol_name}' not found in pool '{pool_name}'")
    # -r: also purges the ZFS snapshots of this zvol (see zfs_snapshot below). A
    # VM/disk deletion must be complete, and never leave orphan snapshots that would
    # prevent recreating the same name later.
    _run("zfs", "destroy", "-r", full_name)


def zvol_in_use_paths():
    """/dev/zvol/... paths of all the zvols on this host, for the same use as
    get_disk_paths_in_use() in libvirt_utils.py (preventing the deletion of a
    pool/zvol that is still referenced). It is combined on the caller side with
    the real list of VM disks (libvirt XML), not duplicated here."""
    paths = set()
    for pool in list_pools():
        for zvol in list_zvols(pool["nom"]):
            paths.add(zvol["chemin"])
    return paths


# --- Snapshots ---------------------------------------------------------
#
# NATIVE ZFS mechanism (`zfs snapshot`/`rollback`), completely distinct from the
# INTERNAL qcow2 snapshot used for regular VMs (domain.snapshotCreateXML): a raw
# block disk (zvol) has no file format with built-in snapshot support, so libvirt
# has nothing to offer on it. An important difference in SEMANTICS, documented on
# the router side rather than hidden: a ZFS snapshot ONLY captures the DISK state
# (equivalent to "pulling the plug" at that precise instant, restored as after a
# hard reboot: consistent at the filesystem level thanks to the journal, but
# never a "resume exactly where we stopped" state). It never captures the VM's
# memory, unlike the qcow2 internal snapshot which includes memory when the VM
# is running.


def snapshot_zvols(specs, snap_name):
    """Create an ATOMIC snapshot (a single `zfs snapshot` command with several
    targets) on all the zvols of `specs` (a list of (pool, zvol_name) tuples).
    Important for a multi-disk VM: the disks must all reflect EXACTLY the same
    instant, not a series of snapshots taken one after another (a consistency
    window)."""
    if not specs:
        raise ZfsError("No zvol to snapshot")
    targets = [f"{pool}/{name}@{snap_name}" for pool, name in specs]
    for pool, name in specs:
        if _run("zfs", "list", "-H", f"{pool}/{name}@{snap_name}", check=False).returncode == 0:
            raise ZfsError(f"A snapshot '{snap_name}' already exists for '{pool}/{name}'")
    _run("zfs", "snapshot", *targets)


def list_zvol_snapshots(pool_name, zvol_name):
    """Snapshots of ONE zvol. The caller (vms.py) aggregates over all the zvols of a
    VM and merges by name to present ONE logical snapshot per VM name, in the
    same shape as list_snapshots."""
    full_name = f"{pool_name}/{zvol_name}"
    proc = _run("zfs", "list", "-t", "snapshot", "-H", "-p", "-o", "name,creation", "-r", full_name, check=False)
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        full, creation = parts
        if "@" not in full:
            continue
        result.append({"nom": full.split("@", 1)[1], "creation_epoch": int(creation)})
    return result


def rollback_zvols(specs, snap_name):
    """Restore all the zvols of `specs` to `snap_name`. `-r` forces the removal of
    any snapshot MORE RECENT than the target on that zvol, because ZFS
    otherwise refuses a rollback to a point that is not the most recent (a
    native protection against accidental data loss). A real LIMITATION
    compared to the internal qcow2 snapshot (which lets you move freely between
    snapshots without losing any): restoring an old ZFS snapshot PERMANENTLY
    destroys every more recent snapshot taken since. The model is linear, not a
    tree. This is accepted and documented rather than hidden: it matches the
    "go back in time" mental model most users expect, without the complexity of
    a real version tree."""
    if not specs:
        raise ZfsError("No zvol to restore")
    for pool, name in specs:
        full = f"{pool}/{name}"
        if _run("zfs", "list", "-H", f"{full}@{snap_name}", check=False).returncode != 0:
            raise ZfsNotFoundError(f"Snapshot '{snap_name}' not found for '{full}'")
    for pool, name in specs:
        _run("zfs", "rollback", "-r", f"{pool}/{name}@{snap_name}")


def delete_zvol_snapshot(specs, snap_name):
    """Delete `snap_name` on all the zvols of `specs`. Best-effort per zvol
    (continues even if one of the zvols does not have this snapshot, e.g. added
    to the VM afterwards) instead of failing on the first missing one."""
    errors = []
    for pool, name in specs:
        full = f"{pool}/{name}@{snap_name}"
        proc = _run("zfs", "destroy", full, check=False)
        if proc.returncode != 0 and "dataset does not exist" not in (proc.stderr or ""):
            errors.append(proc.stderr.strip() or f"failed on {full}")
    if errors:
        raise ZfsError("; ".join(errors))
