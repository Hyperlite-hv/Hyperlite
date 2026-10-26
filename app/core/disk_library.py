"""This node's libraries: the ISOs and the disks to import (app/routers/isos.py, app/routers/vm_disks.py), whose files
hyperlite-daemon stores, renames and deletes (app/daemon/ops/disk.py, docs/design/privilege-separation.md, section 13).

An upload goes through the spool, as Proxmox VE's pveproxy writes an upload into a temporary file and pvedaemon copies
it into the storage: the API writes it into SPOOL, a directory it will own once it runs without root, and the daemon
moves it into its library. The spool is on the libraries' file system, so the move is a rename. The daemon takes a
spool file's name, never a path, and only a regular file with one link: the API must not be able to have a link to
another file stored in a library."""

import os
import re
import secrets
import shutil
import stat
import time
import xml.etree.ElementTree as ET

from app.core import paths
from app.core.error_messages import describe_exception
from app.core.safe_paths import safe_child

ISOS = paths.ISOS
IMPORTS = paths.IMPORTED_DISKS
SPOOL = paths.VAR / "spool"
# Root's own: an upload is moved here before it is checked, out of the API's reach, so the API cannot swap it for a
# link between the check and the move into the library.
STAGING = paths.VAR / "spool-staging"
SPOOL_MAX_AGE_S = 24 * 3600

ISO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}")
IMPORT_EXTENSIONS = (".qcow2", ".img", ".raw", ".vmdk", ".vdi", ".vhd", ".vhdx", ".ova")
SPOOL_NAME_RE = re.compile(r"upload-[0-9a-f]{32}\.part")

ISO_NAME_RULE = (
    "Invalid ISO file name (letters, digits, dots, dashes, underscores and +, starting with a letter or a digit)"
)


class LibraryError(Exception):
    """A refusal or a failure, with the daemon's kind and a sentence for the user."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _child(base, name):
    """safe_child, its refusal a LibraryError: a name that leads out of its directory (a link included)."""
    try:
        return safe_child(base, name)
    except ValueError:
        raise LibraryError("refused", "Invalid file name") from None


def directory(library):
    return {"iso": ISOS, "import": IMPORTS}[library]


def check_name(library, name):
    """`name` as a file of the library, or a LibraryError("refused")."""
    if library == "iso":
        if not name.lower().endswith(".iso") or not ISO_NAME_RE.fullmatch(name[:-4]):
            raise LibraryError("refused", ISO_NAME_RULE)
        return name
    if not name or name != os.path.basename(name) or "\0" in name or name.startswith("."):
        raise LibraryError("refused", "Invalid file name")
    if not name.lower().endswith(IMPORT_EXTENSIONS):
        raise LibraryError("refused", f"Unrecognized extension (expected: {', '.join(IMPORT_EXTENSIONS)})")
    return name


def spool_file():
    """A new file name in the spool for an upload, and its path."""
    SPOOL.mkdir(parents=True, exist_ok=True)
    name = f"upload-{secrets.token_hex(16)}.part"
    return name, SPOOL / name


def clean_spool(now=None):
    """Remove the uploads left in the spool for more than a day (an upload the service did not finish): the API's, at
    its start."""
    return _clean(SPOOL, SPOOL_MAX_AGE_S, now)


def clean_staging(now=None):
    """Remove what a stop left being stored: hyperlite-daemon's, at its start, as the staging directory is root's
    alone (the API, www-data from 4e, cannot even list it)."""
    return _clean(STAGING, 0, now)


def _clean(directory_, max_age, now):
    now = time.time() if now is None else now
    removed = 0
    if not directory_.is_dir():
        return 0
    for entry in directory_.iterdir():
        try:
            if not entry.is_dir() and now - entry.lstat().st_mtime >= max_age:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _discard(staged):
    """A staged entry removed, whatever the API had put there: shutil.rmtree never follows a link."""
    if staged.is_dir() and not staged.is_symlink():
        shutil.rmtree(staged, ignore_errors=True)
    else:
        staged.unlink(missing_ok=True)


def _staged(spool_name):
    """The upload moved out of the spool into STAGING, whatever it is, then opened there: an open descriptor on a
    regular file with one link, and its staged path. The API cannot change the entry once it is staged."""
    if not SPOOL_NAME_RE.fullmatch(spool_name):
        raise LibraryError("refused", "Invalid upload")
    source = _child(SPOOL, spool_name)
    STAGING.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged = STAGING / spool_name
    try:
        os.rename(source, staged)
    except FileNotFoundError:
        raise LibraryError("not_found", "The upload is gone") from None
    except OSError as e:
        raise LibraryError("failed", describe_exception(e)) from e
    try:
        fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _discard(staged)
        raise LibraryError("refused", "Invalid upload: not a file of its own") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        _discard(staged)
        raise LibraryError("refused", "Invalid upload: not a file of its own")
    return fd, staged


def store(library, spool_name, name, replace=False):
    """Move an upload from the spool into the library as `name`, root's, readable by QEMU. An ISO never replaces
    another; a disk to import replaces one of the same name, as an upload always did. Returns its size in bytes."""
    check_name(library, name)
    dest = _child(directory(library), name)
    fd, staged = _staged(spool_name)
    try:
        if dest.exists() and not replace:
            what = "An ISO" if library == "iso" else "A disk"
            raise LibraryError("refused", f"{what} named '{name}' already exists: rename or delete it first")
        if os.geteuid() == 0:  # the daemon; the tests run it in their own process, as their own user
            os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o644)
        size = os.fstat(fd).st_size
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(staged, dest)
        except OSError as e:
            raise LibraryError("failed", describe_exception(e)) from e
    finally:
        os.close(fd)
        staged.unlink(missing_ok=True)
    return size


def in_cdrom(conn, path):
    """Whether a VM of `conn` has this file in a CD-ROM drive."""
    import libvirt

    for domain in conn.listAllDomains():
        try:
            root = ET.fromstring(domain.XMLDesc(0))
        except libvirt.libvirtError:
            continue
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") != "cdrom":
                continue
            source = disk.find("source")
            if source is not None and source.get("file") == str(path):
                return True
    return False


def rename_iso(conn, name, new_name):
    """Refused while a VM has it in a CD-ROM drive: its definition names the file."""
    check_name("iso", name)
    check_name("iso", new_name)
    source, target = _child(ISOS, name), _child(ISOS, new_name)
    if not source.is_file():
        raise LibraryError("not_found", f"ISO '{name}' not found")
    if target.exists():
        raise LibraryError("refused", f"An ISO named '{new_name}' already exists")
    if in_cdrom(conn, source):
        raise LibraryError("in_use", "A VM has this ISO in its CD-ROM drive: eject it first (or rename after)")
    try:
        source.rename(target)
    except OSError as e:
        raise LibraryError("failed", f"Rename failed: {describe_exception(e)}") from e
    return target


def delete(conn, library, name):
    """An ISO is kept while a VM has it in a CD-ROM drive; a disk to import is a copy's source only."""
    check_name(library, name)
    path = _child(directory(library), name)
    if not path.is_file():
        raise LibraryError("not_found", f"{'ISO' if library == 'iso' else 'Disk'} '{name}' not found")
    if library == "iso" and in_cdrom(conn, path):
        raise LibraryError("in_use", "ISO in use by a VM, eject it first")
    try:
        path.unlink()
    except OSError as e:
        raise LibraryError("failed", f"Deletion failed: {describe_exception(e)}") from e
    return path
