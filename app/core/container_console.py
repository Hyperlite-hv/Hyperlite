"""What an application container's process prints, kept in a file and readable from the dashboard.

libvirt's LXC driver only offers a console pty, and nothing reads it: a Docker container that stopped by itself
left no trace of why. A small static launcher (native/hl-console.c), copied into the container at /.hyperlite,
opens /.hyperlite/console.log as the process's standard output and error, then execs the image's program, which
stays PID 1. Everything is captured, including what a program prints before failing at once.

The launcher is compiled once with the host's gcc (a dependency of the package) and cached by source hash. On a
host where static linking is not possible (no libc.a), containers still start, without a log, and say so.
"""

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.core.vm_builder import PROJDIR

logger = logging.getLogger(__name__)

SOURCE = Path(__file__).resolve().parent / "native" / "hl-console.c"
BIN_DIR = PROJDIR / "data" / "bin"
IN_CONTAINER = "/.hyperlite"
LAUNCHER = f"{IN_CONTAINER}/hl-console"
LOG = f"{IN_CONTAINER}/console.log"
# A chatty process (an nginx access log on stdout) must not fill the disk: past MAX_BYTES only the last KEEP_BYTES
# are kept (copy-truncate, as logrotate does: the process keeps writing to the same file).
MAX_BYTES = 8 * 1024 * 1024
KEEP_BYTES = 1024 * 1024


def launcher_binary():
    """Path of the compiled launcher on the host, or None when it cannot be built here."""
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]
    target = BIN_DIR / f"hl-console-{digest}"
    if target.exists():
        return target
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        subprocess.run(
            ["gcc", "-static", "-Os", "-s", "-o", str(tmp), str(SOURCE)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        logger.warning("Container console launcher not built (no log for application containers): %s", detail[-300:])
        tmp.unlink(missing_ok=True)
        return None
    tmp.chmod(0o755)
    tmp.replace(target)
    return target


def _safe_dir(rootfs):
    """rootfs/.hyperlite as a real directory: an image could ship it as a link pointing anywhere on the host."""
    folder = Path(rootfs) / IN_CONTAINER.lstrip("/")
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        folder.unlink()
    folder.mkdir(mode=0o755, exist_ok=True)
    return folder


def install(rootfs, uid, gid):
    """Put the launcher and an empty log in the container. Returns True when the log will be captured."""
    binary = launcher_binary()
    if binary is None:
        return False
    folder = _safe_dir(rootfs)
    target = folder / "hl-console"
    if target.is_symlink():
        target.unlink()
    shutil.copyfile(binary, target)
    target.chmod(0o755)
    log = folder / "console.log"
    if log.is_symlink():
        log.unlink()
    log.touch(mode=0o640, exist_ok=True)
    # The process may run as the image's own user: it must be able to write its log.
    os.chown(log, uid, gid)
    return True


def log_path(rootfs):
    return Path(rootfs) / LOG.lstrip("/")


def trim(rootfs):
    path = log_path(rootfs)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= MAX_BYTES:
            return
        with open(path, "rb+") as f:
            f.seek(-KEEP_BYTES, os.SEEK_END)
            tail = f.read()
            f.seek(0)
            f.truncate()
            f.write(b"--- hyperlite: older lines removed (log over 8 MB)\n" + tail)
    except OSError as e:
        logger.warning("Could not trim %s: %s", path, e)


def read_tail(rootfs, lines=300):
    """The last lines of the log, or None when this container has none (created before logs existed, or on a
    host without the launcher)."""
    path = log_path(rootfs)
    if path.is_symlink() or not path.is_file():
        return None
    trim(rootfs)
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 512 * 1024))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]
