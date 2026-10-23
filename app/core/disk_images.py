"""Disk images Hyperlite did not write itself (an imported disk, a volume put in a pool, a backup from another site)
are checked before anything reads them (GHSA-q6pm-r5rj-538c): an image may name other files to be read with it, a
backing file, a qcow2 external data file or a VMDK extent, and qemu-img and libguestfs, run as root here, would read
them. Such an image is refused, but for the backing files a replication chain names inside its own directory
(app/core/replication.py: ../<previous point>/<dev>.qcow2).

`qemu-img info` opens the image only, never its backing file, so asking it is safe.
"""

import json
import subprocess
from pathlib import Path

INFO_TIMEOUT_S = 120
MAX_CHAIN = 256  # a replication chain is cut long before this


class UnsafeImage(ValueError):
    pass


def _info(path):
    try:
        proc = subprocess.run(
            ["qemu-img", "info", "--output=json", "-U", str(path)],
            capture_output=True,
            text=True,
            timeout=INFO_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise UnsafeImage(f"The disk image {Path(path).name} could not be read: {e}") from e
    if proc.returncode != 0:
        raise UnsafeImage(f"The disk image {Path(path).name} could not be read: {(proc.stderr or '').strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except ValueError as e:
        raise UnsafeImage(f"The disk image {Path(path).name} could not be read") from e


def check(path, within=None):
    """The image's format, once neither it nor its backing chain names another file: none at all, or with `within`
    (a directory), backing files inside it only. Raises UnsafeImage."""
    image = Path(path).resolve()
    root = Path(within).resolve() if within is not None else None
    current, seen, fmt = image, {image}, None
    for _ in range(MAX_CHAIN):
        info = _info(current)
        fmt = fmt or info.get("format")
        data = (info.get("format-specific") or {}).get("data") or {}
        if data.get("data-file"):
            raise UnsafeImage(f"The disk image {image.name} names an external data file: refused")
        for extent in data.get("extents") or []:
            name = extent.get("filename")
            if name and Path(name).resolve() != current:
                raise UnsafeImage(f"The disk image {image.name} names another file as one of its extents: refused")
        backing = info.get("full-backing-filename") or info.get("backing-filename")
        if not backing:
            return fmt
        target = None
        if root is not None and not backing.startswith("json:") and "://" not in backing:
            target = (current.parent / backing).resolve()
        if target is None or not target.is_relative_to(root) or target in seen:
            raise UnsafeImage(
                f"The disk image {image.name} is based on another file: refused (convert it to a standalone image)"
            )
        seen.add(target)
        current = target
    raise UnsafeImage(f"The disk image {image.name} has too long a chain of backing files")
