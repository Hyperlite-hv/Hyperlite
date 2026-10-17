"""Room on a file system before a large copy (a backup, a restore, a clone). A copy that fills its file system fails
late, after hours, and when that file system is the system disk it also stops Hyperlite's database, the journal and
libvirt. Refused before the first byte instead, with a reserve kept free."""

import json
import os
import shutil
import subprocess
from pathlib import Path

FREE_RESERVE_BYTES = 1 << 30
_GIB = 1024**3


class NotEnoughSpace(RuntimeError):
    pass


def allocated_bytes(path):
    """What a disk occupies with its backing chain: qemu-img convert writes the chain flattened, unused blocks left
    out, so its output is at most this."""
    try:
        r = subprocess.run(
            ["qemu-img", "info", "--backing-chain", "--output=json", "-U", str(path)],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):  # qemu-img missing or stuck: the file's own blocks below
        r = None
    if r is not None and r.returncode == 0:
        try:
            return sum(int(layer.get("actual-size") or 0) for layer in json.loads(r.stdout))
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        return os.stat(path).st_blocks * 512
    except OSError:
        return 0


def ensure_room(directory, needed, what):
    """Raise NotEnoughSpace unless the file system of `directory` (or of its nearest existing parent, where a
    directory still to be created will land) has `needed` bytes free, FREE_RESERVE_BYTES kept aside. Creates nothing."""
    directory = Path(directory)
    existing = next((d for d in (directory, *directory.parents) if d.is_dir()), Path("/"))
    free = shutil.disk_usage(existing).free
    if free < needed + FREE_RESERVE_BYTES:
        raise NotEnoughSpace(
            f"Not enough space for {what} in {directory}: {free / _GIB:.1f} GiB free, about {needed / _GIB:.1f} GiB "
            f"needed and {FREE_RESERVE_BYTES / _GIB:.0f} GiB kept free"
        )
