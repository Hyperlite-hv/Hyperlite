"""The consoles' sessions, opened by hyperlite-daemon (docs/design/privilege-separation.md, section 13, the consoles
domain): a program in a new pseudo-terminal (a shell, `virsh console`, ssh to a guest), or a socket connected to a VM's
display, whose descriptor the daemon hands to the API (app/daemon/registry.py, Handover). The API bridges it to the
browser; the bytes never cross the daemon's socket.

The daemon keeps each session's process until the API closes it (close) or it ends; its own copy of the descriptor is
closed once handed over, so a terminal hangs up, and ssh reads the end of its input, as soon as the API lets go of
it, even when the API itself stops."""

import contextlib
import fcntl
import logging
import os
import pty
import shlex
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# How long a session's program has to end on its own once the API let go of it (a terminal hung up, ssh at the end of
# its input), then after SIGTERM before SIGKILL, as the API's own sessions did.
SETTLE_S = 0.5
GRACE_S = 3
# How long an ended session's exit status waits for the API's close, which turns it into a message.
KEEP_ENDED_S = 60
CONNECT_TIMEOUT_S = 10

_sessions = {}  # id -> subprocess.Popen
_ended = {}  # id -> when its process ended (time.monotonic())
_lock = threading.Lock()


def set_size(fd, cols, rows):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _new_session():
    """In the child: a session of its own whose controlling terminal is the pty, so the program has real terminal
    control (Ctrl+C, job control, `top`, `vim`). Without TIOCSCTTY, bash starts with "no job control in this shell"."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _keep(proc, errors=None):
    sid = uuid.uuid4().hex
    now = time.monotonic()
    with _lock:
        for old, ended in list(_ended.items()):
            if now - ended > KEEP_ENDED_S:
                _ended.pop(old, None)
                _sessions.pop(old, None)
        _sessions[sid] = proc
    threading.Thread(target=_reap, args=(sid, proc, errors), daemon=True, name="console-session").start()
    return sid


def _reap(sid, proc, errors):
    """Wait for the session's process (no zombie is left), with what a relay's ssh said when it failed."""
    said = errors.read().decode(errors="replace").strip() if errors is not None else ""
    code = proc.wait()
    if errors is not None:
        errors.close()
    if code and said:
        logger.warning("console session %s ended (%d): %s", sid[:8], code, said)
    with _lock:
        if sid in _sessions:
            _ended[sid] = time.monotonic()


def open_pty(argv, env, cols=80, rows=24):
    """argv in a new pseudo-terminal of its own session: (the terminal's master descriptor, the session's id)."""
    master, slave = pty.openpty()
    set_size(master, cols, rows)
    try:
        proc = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave, preexec_fn=_new_session, env=env, close_fds=True
        )
    except OSError:
        os.close(master)
        raise
    finally:
        # Only the child keeps the terminal's slave end: the master then reads the end of the program.
        os.close(slave)
    return master, _keep(proc)


def open_relay(argv):
    """argv (`ssh -W`) speaking on its input and output to one end of a socket pair: (the other end's descriptor, the
    session's id)."""
    ours, theirs = socket.socketpair()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=ours.fileno(),
            stdout=ours.fileno(),
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        theirs.close()
        raise
    finally:
        ours.close()
    return theirs.detach(), _keep(proc, proc.stderr)


def connect_local(port):
    """A socket connected to a port of this node's loopback (a VM's display): its descriptor."""
    sock = socket.create_connection(("127.0.0.1", int(port)), timeout=CONNECT_TIMEOUT_S)
    sock.settimeout(None)
    return sock.detach()


def close(sid):
    """End a session's whole process group (SIGTERM, SIGKILL after GRACE_S): its exit status, None for a session this
    daemon does not hold (already closed, or opened before a restart)."""
    with _lock:
        proc = _sessions.pop(sid, None)
        _ended.pop(sid, None)
    if proc is None:
        return None
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(SETTLE_S)  # its own exit status (ssh's 255 when it could not connect), not SIGTERM's
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(GRACE_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    return proc.returncode


def open_count():
    with _lock:
        return sum(1 for proc in _sessions.values() if proc.poll() is None)


# --- The programs ---

PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def root_environment(**extra):
    """A clean environment for root's programs: never the daemon's own (nor the API's, which held its secrets)."""
    import pwd

    root = pwd.getpwuid(0)
    env = {
        "HOME": root.pw_dir,
        "USER": root.pw_name,
        "LOGNAME": root.pw_name,
        "SHELL": root.pw_shell or "/bin/sh",
        "PATH": PATH,
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
    }
    env.update(extra)
    return env


def node_ssh(node, *options):
    """ssh to a registered node with the cluster's key, its host key the one recorded when it was registered (never
    learned here: an unknown or changed key is refused). ssh takes the first value of an option, so these come before
    the cluster's defaults."""
    from app.core import node_health
    from app.core.cluster import check_address, node_ssh_options

    check_address(node["hostname"], node["ssh_user"])
    return [
        "ssh",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={node_health.CONNECT_TIMEOUT_S}",
        *options,
        *node_ssh_options(),
        "-p",
        str(int(node["ssh_port"])),
    ]


def display_relay(node, port):
    """`ssh -W` to a port of a node's loopback, where QEMU serves a VM's display."""
    return [*node_ssh(node), "-W", f"127.0.0.1:{int(port)}", "--", f"{node['ssh_user']}@{node['hostname']}"]


def guest_ssh(address, user, node=None):
    """ssh to a guest with the automation key, in a terminal; through its node for a VM of another node (the guest's
    address is on that node's networks). The guest's host key is not checked, as before: it is made at its first boot
    and Hyperlite never recorded it."""
    from app.core.vm_builder import get_automation_private_key_path

    argv = [
        "ssh",
        "-tt",
        "-i",
        str(get_automation_private_key_path()),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"ConnectTimeout={CONNECT_TIMEOUT_S}",
    ]
    if node is not None:
        hop = [*node_ssh(node), "-W", "%h:%p", "--", f"{node['ssh_user']}@{node['hostname']}"]
        argv += ["-o", "ProxyCommand=" + shlex.join(hop)]
    return [*argv, "--", f"{user}@{address}"]
