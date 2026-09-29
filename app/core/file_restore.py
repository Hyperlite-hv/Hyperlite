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

from app.core import backup_integrity
from app.core.database import get_conn

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
        "disponible": python is not None,
        "raison": None
        if python
        else "Install libguestfs-tools and python3-guestfs on this node (apt install libguestfs-tools python3-guestfs)",
    }


def backup_disks(backup_id):
    with get_conn() as conn:
        row = conn.execute("SELECT id, vm_name, chemin, statut FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if not row:
        raise RestoreError("Backup not found")
    if row["statut"] != "termine":
        raise RestoreError("Only a finished backup can be browsed")
    root = Path(row["chemin"])
    manifest = backup_integrity.read_manifest(root)
    if manifest:
        names = [f["nom"] for f in manifest.get("fichiers", []) if f.get("role") == "disque"]
    else:
        names = sorted(p.name for p in root.iterdir() if p.suffix in DISK_SUFFIXES) if root.is_dir() else []
    disks = [root / n for n in names if "/" not in n and (root / n).is_file()]
    if not disks:
        raise RestoreError("This backup has no disk image left to browse")
    return row["vm_name"], [str(d) for d in disks]


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


def open_session(backup_id, username):
    if interpreter() is None:
        raise RestoreError(status()["raison"])
    vm_name, disks = backup_disks(backup_id)
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
    session = Session(backup_id, vm_name, disks, username)
    with _lock:
        _sessions[session.id] = session
    return session


def get_session(session_id, username):
    with _lock:
        s = _sessions.get(session_id)
    if s is None or s.username != username:
        raise RestoreError("This browsing session does not exist or has ended: open the backup again")
    return s


def close_session(session_id, username):
    s = get_session(session_id, username)
    with _lock:
        _sessions.pop(session_id, None)
    s.close()


def export(session, device, path):
    """Copy a file, or a folder as a .tar.gz, out of the backup into a temporary file. Returns (temp path, name)."""
    fd, dest = tempfile.mkstemp(prefix="export-", dir=session.workdir)
    os.close(fd)
    res = session.request({"op": "export", "device": device, "path": path, "dest": dest})
    base = PurePosixPath(path).name or "root"  # guest paths are POSIX, NTFS included, as libguestfs shows them
    return dest, f"{base}.tar.gz" if res["kind"] == "dir" else base
