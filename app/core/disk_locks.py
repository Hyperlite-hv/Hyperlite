"""Whether a lock on a disk reaches the other nodes (docs/design/ha-automatic.md, 4.7).

QEMU locks every image it opens, and on a share that passes locks to its server another node is refused the disk while
one runs it: two copies of a VM on its shared disk would corrupt it. Checked in the lab on NFS 4.2: refused, then
released once the VM stopped, or 94 s after its node died (the NFSv4 lease). A mount that keeps its locks on the node
that took them protects nothing between nodes: NFS with `nolock` or a `local_lock=` other than `none`, SMB with
`nobrl`. HA refuses a disk on such a mount, and a definition that turns QEMU's locks off.
"""

from pathlib import Path

MOUNTINFO = Path("/proc/self/mountinfo")


def _unescape(field):
    """A mount point as /proc/self/mountinfo escapes it (space, tab, newline, backslash in octal)."""
    for code, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field = field.replace(code, char)
    return field


def _mounts():
    """(mount point, filesystem type, its options) of each mount of this node."""
    out = []
    for line in MOUNTINFO.read_text().splitlines():
        left, _, right = line.partition(" - ")
        fields, rest = left.split(), right.split()
        if len(fields) < 6 or len(rest) < 2:
            continue
        # The per-mount options (field 6) and the filesystem's own (the last one): NFS puts nolock and local_lock in
        # the latter, a bind mount may carry either.
        out.append((_unescape(fields[4]), rest[0], set(fields[5].split(",")) | set(rest[-1].split(","))))
    return out


def mount_of(path):
    """(mount point, filesystem type, options) of the mount `path` is on, or None."""
    best = None
    for point, fstype, options in _mounts():
        if (path == point or path.startswith(point.rstrip("/") + "/")) and (best is None or len(point) > len(best[0])):
            best = (point, fstype, options)
    return best


def local_only(path):
    """None when a lock on `path` reaches the other nodes; else why it does not."""
    mount = mount_of(path)
    if mount is None:
        return None
    point, fstype, options = mount
    if fstype.startswith("nfs"):
        if "nolock" in options:
            return f"{point} is mounted with nolock: a lock taken there stays on this node"
        local = sorted(o for o in options if o.startswith("local_lock=") and o != "local_lock=none")
        if local:
            return f"{point} is mounted with {local[0]}: a lock taken there stays on this node"
    elif fstype in ("cifs", "smb3") and "nobrl" in options:
        return f"{point} is mounted with nobrl: a lock taken there stays on this node"
    return None


def locking_off(domain_xml):
    """Whether a definition turns QEMU's image locks off (a disk option `locking=off`)."""
    return "locking=off" in domain_xml
