"""Live restore (docs/design/live-restore.md): a backup restored as a new VM that starts at once.

Each restored disk is a new qcow2 file whose backing file is the backup's own image, read only by QEMU: the VM boots
on it straight away, its writes go to the new file, its reads of what it has not written come from the backup. Then
libvirt's block pull (QEMU's block-stream) copies the rest into the new file, in the background, and drops the backing
reference: the disk no longer needs the backup. A copy that fails stops the VM and removes it, its new disks with it:
a disk is never left half restored. Run as root, by hyperlite-daemon (app/core/backups.py, restore_files).
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

POLL_S = 1.0


class LiveRestoreError(RuntimeError):
    pass


def overlay(backup_image, dest):
    """A new qcow2 file at `dest` reading through to `backup_image` (never written: QEMU opens a backing file read-only)."""
    backup_image = Path(backup_image).resolve()
    info = image_info(backup_image)
    if info.get("format") != "qcow2":
        raise LiveRestoreError(f"{backup_image.name} is not a qcow2 image: a live restore needs one")
    subprocess.run(
        ["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(backup_image), str(dest)],
        check=True,
        capture_output=True,
    )


def overlay_nbd(uri, size, dest):
    """A new qcow2 file at `dest` reading through to the NBD export `uri` of `size` bytes (a backup in a store served
    by app/core/nbd_export.py). Not opened here (-u): QEMU opens it once the VM starts."""
    subprocess.run(
        ["qemu-img", "create", "-q", "-u", "-f", "qcow2", "-F", "raw", "-b", uri, str(dest), str(size)],
        check=True,
        capture_output=True,
    )


def image_info(path):
    r = subprocess.run(
        ["qemu-img", "info", "-U", "--output=json", str(path)], capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        raise LiveRestoreError(f"{Path(path).name}: {(r.stderr or r.stdout).strip()[-200:]}")
    return json.loads(r.stdout)


class Owners:
    """libvirt gives a running VM's backing files to QEMU's account and does not give them back: the backup's images
    are given back their owner once the copy is done, whatever happened."""

    def __init__(self, paths):
        self.saved = {}
        for p in paths:
            st = os.stat(p)
            self.saved[str(p)] = (st.st_uid, st.st_gid, st.st_mode & 0o7777)

    def restore(self):
        for p, (uid, gid, mode) in self.saved.items():
            try:
                os.chown(p, uid, gid)
                os.chmod(p, mode)
            except OSError as e:
                logger.warning("Owner of %s not given back: %s", p, e)


def stream(domain, targets, progress=None, timeout_s=24 * 3600):
    """Copy every disk `targets` (["vda", ...]) of the running `domain` out of its backing file. progress(0..100)."""
    import libvirt

    for dev in targets:
        domain.blockPull(dev, 0, 0)
    deadline = time.monotonic() + timeout_s
    pending = list(targets)
    while pending:
        done_bytes = total = 0
        for dev in list(pending):
            try:
                info = domain.blockJobInfo(dev, 0)
            except libvirt.libvirtError as e:
                raise LiveRestoreError(f"The copy of {dev} stopped: {e.get_error_message()}") from e
            if not info:
                pending.remove(dev)
                continue
            done_bytes += info.get("cur", 0)
            total += info.get("end", 0)
        if progress and total:
            progress(int(100 * done_bytes / total))
        if time.monotonic() > deadline:
            raise LiveRestoreError("The copy did not end in time")
        if pending:
            time.sleep(POLL_S)
    if progress:
        progress(100)


def check_standalone(paths):
    """Every restored disk holds its own data: no backing file left (a copy that ended without finishing)."""
    for p in paths:
        backing = image_info(p).get("backing-filename")
        if backing:
            raise LiveRestoreError(f"{Path(p).name} still reads from {backing}: the copy did not finish")
