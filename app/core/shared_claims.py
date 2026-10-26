"""Claims shared by every process that works on a guest: the API and the schedulers (docs/design/privilege-separation.md,
section 14, 4c), as Proxmox VE locks a guest with a file of /run/lock/qemu-server that each of its services takes.

A claim is an exclusive flock(2) on a file of paths.LOCKS, held by an open descriptor for as long as the operation
runs. The kernel releases it when the descriptor is closed, so a process that stops, or is killed, leaves no claim
behind. The file names what holds it, for a refusal's message: it is emptied before the lock is let go, and a name
left by a process that was killed is recognised as stale because nobody holds its lock.

The files are never removed: removing one while another process opens it would give two holders two different files.
They are a few bytes each, and /run is emptied at boot.

This process's own claims are also kept in memory: they answer without reading a file."""

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from urllib.parse import quote

from app.core import api_group, paths
from app.core.safe_paths import safe_child

# How long a claim retries a lock it could not take before it is refused: a reader checking whether a name left by a
# killed process is stale holds the lock for an instant, as does a holder between emptying its file and letting go.
SETTLE_S = 0.2
_SETTLE_STEP_S = 0.01
_NAME_LIMIT = 200

_lock = threading.Lock()
_held = {}  # (kind, node, name) -> (what, descriptor)


class Busy(RuntimeError):
    def __init__(self, name, running):
        super().__init__(name, running)
        self.name, self.running = name, running


def _directory():
    """paths.LOCKS, made if missing. It must be root's or this service's, and nobody else may write in it: /run/lock is
    writable by every account, and a file planted there for root to empty would be any file of the system."""
    directory = paths.LOCKS
    with contextlib.suppress(FileExistsError):
        directory.mkdir(mode=0o770, parents=True)
        # Root's services and the API take claims there (installer/hyperlite.tmpfiles makes it so at boot).
        api_group.share(directory, 0o770)
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, os.geteuid()) or st.st_mode & stat.S_IWOTH:
        raise RuntimeError(f"{directory} is not a directory of root's or this service's alone: no claim is taken there")
    return directory


def _file(key):
    kind, node, name = key
    readable = f"{kind}@{quote(node, safe='')}@{quote(name, safe='')}.lock"
    if len(readable) > _NAME_LIMIT:
        readable = f"{kind}@sha256-{hashlib.sha256(node.encode() + b'\0' + name.encode()).hexdigest()}.lock"
    # Quoted, the names hold no "/": safe_child is the last line of defence all the same.
    return safe_child(_directory(), readable)


def _open(path, flags):
    """O_NOFOLLOW, and a regular file only: the API's group writes in the directory too, and root never follows a link
    or opens a FIFO or a device found there. A file made here is root's and the API's group's (0660)."""
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o660)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        if flags & os.O_CREAT and st.st_mode & 0o777 != 0o660 and st.st_uid == os.geteuid():
            api_group.share_fd(fd, 0o660)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read(fd):
    """What the file says holds it: {"node", "name", "what"}, or None when it is empty or unreadable."""
    try:
        data = os.pread(fd, 4096, 0)
        return json.loads(data) if data else None
    except (OSError, ValueError):
        return None


def _holder(path):
    """What holds the lock of `path` in another process, or None when nobody holds it."""
    try:
        fd = _open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        said = _read(fd)
        if said is None:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return said
        return None  # a name left by a process that was killed: nobody holds it
    finally:
        os.close(fd)


def take(kind, node, name, what):
    """Claim `name` (a VM, a container) of `node` for `what`: its descriptor's key. Raises Busy when another operation
    holds it, in this process or another."""
    key = (kind, node, name)
    with _lock:
        if key in _held:
            raise Busy(name, _held[key][0])
        fd = _open(_file(key), os.O_RDWR | os.O_CREAT)
        try:
            deadline = time.monotonic() + SETTLE_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        said = _read(fd) or {}
                        raise Busy(name, said.get("what") or "another operation") from None
                    time.sleep(_SETTLE_STEP_S)
            os.ftruncate(fd, 0)
            os.pwrite(fd, json.dumps({"node": node, "name": name, "what": what, "pid": os.getpid()}).encode(), 0)
        except BaseException:
            os.close(fd)
            raise
        _held[key] = (what, fd)
    return key


def release(key):
    """Let go of a claim taken here; a key already released is ignored."""
    with _lock:
        held = _held.pop(key, None)
    if held is None:
        return
    fd = held[1]
    try:
        # Emptied first: whoever reads the file between the two then sees no holder, and retries.
        os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def holder(kind, node, name):
    """What holds `name` of `node`, here or in another process, or None."""
    key = (kind, node, name)
    with _lock:
        if key in _held:
            return _held[key][0]
    said = _holder(_file(key))
    return said and said.get("what")


def held_on(kind, node):
    """What holds the claims of `kind` on `node`, in every process."""
    with _lock:
        ours = {key: what for key, (what, _fd) in _held.items()}
    found = [what for (k, n, _name), what in ours.items() if k == kind and n == node]
    for path in _directory().glob(f"{kind}@*.lock"):
        said = _holder(path)
        if said and said.get("node") == node and (kind, node, said.get("name")) not in ours:
            found.append(said.get("what"))
    return found


def release_all():
    """Every claim of this process let go of (the test suite, between two tests)."""
    with _lock:
        keys = list(_held)
    for key in keys:
        release(key)
