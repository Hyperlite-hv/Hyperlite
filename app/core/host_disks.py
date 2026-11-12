"""The node's disks that hold nothing (docs/design/zfs-disks.md): what a ZFS pool may be built on
Disks > ZFS lists them. A disk is free when it is a whole disk with no partition, no file system or volume signature
(LVM, ZFS, RAID, swap...), nothing mounted, not read-only, not removable, and no VM's disk. Anything else is never
offered and refused if named: building a pool erases the disk.

Disks are named by their /dev/disk/by-id path, which stays the same across reboots and controllers (/dev/sdb may not)."""

import json
import os
import subprocess
from pathlib import Path

BY_ID = Path("/dev/disk/by-id")
MIN_BYTES = 1 << 30
# The stable names, best first: the bus and the serial, then the WWN; never a partition's.
_PREFERRED = ("nvme-", "ata-", "scsi-", "wwn-", "usb-", "virtio-")


def _lsblk():
    out = subprocess.run(
        ["lsblk", "-J", "-b", "-p", "-o", "NAME,TYPE,SIZE,MODEL,SERIAL,ROTA,RM,RO,FSTYPE,MOUNTPOINTS"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    return json.loads(out).get("blockdevices", [])


def stable_name(device, by_id=BY_ID):
    """/dev/disk/by-id/<name> of `device` (/dev/sdb), or the device itself when it has none."""
    try:
        links = [
            p for p in by_id.iterdir() if "-part" not in p.name and os.path.realpath(p) == os.path.realpath(device)
        ]
    except OSError:
        links = []

    def rank(p):
        return next((i for i, prefix in enumerate(_PREFERRED) if p.name.startswith(prefix)), len(_PREFERRED)), p.name

    return str(min(links, key=rank)) if links else str(device)


def _used(dev):
    if dev.get("fstype") or dev.get("children") or [m for m in dev.get("mountpoints") or [] if m]:
        return True
    return bool(dev.get("ro")) or bool(dev.get("rm"))


def free_disks(in_use=(), devices=None, by_id=BY_ID):
    """[{"path" (by-id), "device", "size", "model", "serial", "rotational"}] of the disks a pool may take. `in_use`: the
    paths VMs' disks name (any form: /dev/sdb or a by-id link)."""
    used = {os.path.realpath(p) for p in in_use}
    found = []
    for dev in _lsblk() if devices is None else devices:
        if dev.get("type") != "disk" or _used(dev) or int(dev.get("size") or 0) < MIN_BYTES:
            continue
        if os.path.realpath(dev["name"]) in used:
            continue
        found.append(
            {
                "path": stable_name(dev["name"], by_id),
                "device": dev["name"],
                "size": int(dev["size"]),
                "model": (dev.get("model") or "").strip(),
                "serial": (dev.get("serial") or "").strip(),
                "rotational": bool(dev.get("rota")),
            }
        )
    return found
