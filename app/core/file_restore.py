"""File-level restore: browse a backup's disks and download files or folders from them, without restoring the VM.

Each browsing session is a helper process (app/core/file_restore_helper.py) that keeps one libguestfs handle open
on the backup's disk images, read-only: starting the libguestfs appliance takes seconds (tens without KVM), so it
is paid once per session instead of at each folder. The helper runs under the system Python that has the
distribution's python3-guestfs; the service's virtual environment does not carry it.

Sessions belong to the administrator who opened them, are few (each appliance is a small VM with its own memory)
and close after IDLE_S without a request. The backup itself is never written: libguestfs opens it read-only.
"""

import contextlib
import glob
import json
import logging
import os
import secrets
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

from app.core import backup_integrity, disk_images

logger = logging.getLogger(__name__)

HELPER = Path(__file__).with_name("file_restore_helper.py")
MAX_SESSIONS = 2
IDLE_S = 600
START_TIMEOUT_S = 180
REQUEST_TIMEOUT_S = 300
DISK_SUFFIXES = (".qcow2", ".img", ".raw")

_sessions = {}
_lock = threading.Lock()
_interpreter = []  # cached [path or None] once looked up
_reaper = []


class RestoreError(RuntimeError):
    """A request refused or failed, with a message for the user."""


def interpreter():
    """A Python that can import the guestfs bindings (the distribution's own), or None."""
    if _interpreter:
        return _interpreter[0]
    candidates = [os.environ.get("HYPERLITE_GUESTFS_PYTHON"), "/usr/bin/python3"]
    candidates += sorted(glob.glob("/usr/bin/python3.[0-9]*"), reverse=True)
    found = None
    for path in candidates:
        if not path or path.endswith("-config") or not os.access(path, os.X_OK):
            continue
        try:
            ok = subprocess.run([path, "-c", "import guestfs"], capture_output=True, timeout=15).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            found = path
            break
    _interpreter.append(found)
    return found


def status():
    python = interpreter()
    return {
        "available": python is not None,
        "reason": None
        if python
        else "Install libguestfs-tools and python3-guestfs on this node (apt install libguestfs-tools python3-guestfs)",
    }


def backup_row(backup_id):
    """The API's part: the backup's record, checked (finished, a directory backup)."""
    from app.repositories import registry

    row = registry.backups().sync.get(backup_id)
    if not row:
        raise RestoreError("Backup not found")
    if row["status"] != "succeeded":
        raise RestoreError("Only a finished backup can be browsed")
    if row.get("store"):
        raise RestoreError("Files cannot be browsed in a backup kept in a backup store yet: restore its VM instead")
    return row


def backup_disks_at(path):
    """hyperlite-daemon's part: the disk images of the backup at `path`, a directory backups.backup_dir accepts, each
    checked (libguestfs would read whatever file an image names)."""
    from app.core import backups

    try:
        root = backups.backup_dir(path)
    except ValueError as e:
        raise RestoreError(str(e)) from None
    manifest = backup_integrity.read_manifest(root)
    if manifest:
        names = [f["name"] for f in manifest.get("files", []) if f.get("role") == "disk"]
    else:
        names = sorted(p.name for p in root.iterdir() if p.suffix in DISK_SUFFIXES) if root.is_dir() else []
    disks = [root / n for n in names if "/" not in n and (root / n).is_file()]
    if not disks:
        raise RestoreError("This backup has no disk image left to browse")
    for disk in disks:
        try:
            disk_images.check(disk, within=root.parent)
        except disk_images.UnsafeImage as e:
            raise RestoreError(str(e)) from None
    return [str(d) for d in disks]


class Session:
    def __init__(self, backup_id, vm_name, disks, username):
        self.id = secrets.token_urlsafe(18)
        self.backup_id, self.vm_name, self.username = backup_id, vm_name, username
        self.lock = threading.Lock()
        self.last = time.monotonic()
        self.workdir = Path(tempfile.mkdtemp(prefix="hyperlite-filerestore-"))
        self.errlog = open(self.workdir / "helper.log", "w+")  # noqa: SIM115 -- kept open for the helper's lifetime
        self.proc = subprocess.Popen(
            [interpreter(), str(HELPER), *disks],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errlog,
            text=True,
            env={**os.environ, "LIBGUESTFS_BACKEND": "direct"},
        )
        first = self._read(START_TIMEOUT_S)
        if not first.get("ready"):
            self.close()
            raise RestoreError(first.get("error") or "The backup's disks could not be opened")
        self.filesystems = self.request({"op": "filesystems"})["filesystems"]

    def _stderr_tail(self):
        with contextlib.suppress(OSError):
            self.errlog.flush()
            self.errlog.seek(0)
            return self.errlog.read()[-400:].strip()
        return ""

    def _read(self, timeout):
        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            if not sel.select(timeout):
                raise RestoreError("The backup browser did not answer in time")
        finally:
            sel.close()
        line = self.proc.stdout.readline()
        if not line:
            detail = self._stderr_tail()
            raise RestoreError(f"The backup browser stopped{': ' + detail if detail else ''}")
        return json.loads(line)

    def request(self, req, timeout=REQUEST_TIMEOUT_S):
        with self.lock:
            if self.proc.poll() is not None:
                raise RestoreError("This browsing session has ended: open the backup again")
            self.last = time.monotonic()
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            res = self._read(timeout)
            self.last = time.monotonic()
        if "error" in res:
            raise RestoreError(res["error"])
        return res

    def close(self):
        with contextlib.suppress(OSError, ValueError, BrokenPipeError):
            self.proc.stdin.write(json.dumps({"op": "close"}) + "\n")
            self.proc.stdin.flush()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        with contextlib.suppress(OSError):
            self.errlog.close()
        shutil.rmtree(self.workdir, ignore_errors=True)


def _reap():
    while True:
        time.sleep(30)
        with _lock:
            idle = [s for s in _sessions.values() if time.monotonic() - s.last > IDLE_S or s.proc.poll() is not None]
            for s in idle:
                _sessions.pop(s.id, None)
        for s in idle:
            try:
                s.close()
            except Exception:
                logger.exception("Closing an idle file-restore session failed")


def is_open_here(backup_id):
    """Whether a live file-restore session of this process reads this backup: hyperlite-daemon's."""
    with _lock:
        return any(s.backup_id == backup_id and s.proc.poll() is None for s in _sessions.values())


def open_here(backup_id, path, username):
    """hyperlite-daemon's part of open_session (restore.open): a session on the backup at `path`, or the one this user
    already has on it."""
    if interpreter() is None:
        raise RestoreError(status()["reason"])
    disks = backup_disks_at(path)
    with _lock:
        for s in _sessions.values():
            if s.backup_id == backup_id and s.username == username and s.proc.poll() is None:
                s.last = time.monotonic()
                return s
        if len(_sessions) >= MAX_SESSIONS:
            raise RestoreError(
                f"{MAX_SESSIONS} backups are being browsed already: close one first (or wait for it to time out)"
            )
        if not _reaper:
            t = threading.Thread(target=_reap, name="file-restore-reaper", daemon=True)
            t.start()
            _reaper.append(t)
    session = Session(backup_id, None, disks, username)
    with _lock:
        _sessions[session.id] = session
    return session


def session_here(session_id, username):
    with _lock:
        s = _sessions.get(session_id)
    if s is None or s.username != username:
        raise RestoreError("This browsing session does not exist or has ended: open the backup again")
    return s


def close_here(session_id, username):
    s = session_here(session_id, username)
    with _lock:
        _sessions.pop(session_id, None)
    s.close()


def export_here(session, device, path):
    """A file, or a folder as a .tar.gz, copied out of the backup: (an open descriptor of the copy, its name). The
    copy is unlinked once opened: it lives as long as the descriptor the API reads it from."""
    fd, dest = tempfile.mkstemp(prefix="export-", dir=session.workdir)
    os.close(fd)
    try:
        res = session.request({"op": "export", "device": device, "path": path, "dest": dest})
        opened = os.open(dest, os.O_RDONLY)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(dest)
    base = PurePosixPath(path).name or "root"  # guest paths are POSIX, NTFS included, as libguestfs shows them
    return opened, f"{base}.tar.gz" if res["kind"] == "dir" else base


# --- The API's side: hyperlite-daemon holds the sessions (restore.*), the API their records ---


class Opened:
    """A session as the API knows it."""

    def __init__(self, session_id, backup_id, vm_name, filesystems, username):
        self.id, self.backup_id, self.vm_name, self.filesystems = session_id, backup_id, vm_name, filesystems
        self.username = username


_opened = {}  # session id -> Opened, for the API's audit entries


def _daemon(op, username, fds=False, **args):
    from app.core import privileged
    from app.daemon.registry import DaemonError

    try:
        run = privileged.run_fds if fds else privileged.run
        return run(op, user=username, timeout=START_TIMEOUT_S + 30, username=username, **args)
    except DaemonError as e:
        raise RestoreError(e.detail) from None


def open_session(backup_id, username):
    row = backup_row(backup_id)
    found = _daemon("restore.open", username, backup_id=backup_id, path=row["path"] or "")
    opened = Opened(found["session"], backup_id, row["vm_name"], found["filesystems"], username)
    _opened[opened.id] = opened
    return opened


def get_session(session_id, username):
    opened = _opened.get(session_id)
    if opened is None or opened.username != username:
        raise RestoreError("This browsing session does not exist or has ended: open the backup again")
    return opened


def list_dir(session_id, username, device, path):
    return _daemon("restore.ls", username, session=session_id, device=device, path=path)


def export(session_id, username, device, path):
    """(an open descriptor of the file, or of the folder as a .tar.gz, its name): the caller closes it."""
    result, fds = _daemon("restore.export", username, fds=True, session=session_id, device=device, path=path)
    if not fds:
        raise RestoreError("The file could not be read out of the backup")
    from app.core import daemon_client

    daemon_client.close_fds(fds[1:])
    return fds[0], result["name"]


def close_session(session_id, username):
    _opened.pop(session_id, None)
    _daemon("restore.close", username, session=session_id)


def is_open(backup_id):
    """Whether a live file-restore session reads this backup (it must not be deleted under it), as hyperlite-daemon
    holds them."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    try:
        return privileged.run("restore.busy", backup_id=backup_id)["open"]
    except DaemonError:
        return False
