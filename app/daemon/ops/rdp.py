"""Remote desktop sessions (docs/design/console-2.md, section 8): hyperlite-daemon starts, per session, a transient unit
that speaks RDP to the guest and shows it on a VNC display reached through a Unix socket only, then hands the API a
socket connected to that display, as for a VM's console. The guest's address is read here from the VM itself: the API
names a VM, never where to connect."""

import os
import shutil
import socket
import subprocess
import time

from pydantic import Field

from app.daemon.needs import ADMIN, on_vm
from app.daemon.registry import Args, DaemonError, Handover, operation

SID = Field(min_length=16, max_length=16, pattern=r"^[a-f0-9]{16}$")
NODE = Field(default=None, min_length=1, max_length=63)
START_WAIT_S = 15


def _run(argv, timeout=60):
    """A command's result (a seam the tests replace)."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def missing_tools():
    from app.core import rdp_box

    return [t for t in rdp_box.TOOLS if shutil.which(t) is None]


def _guest_address(vm, node):
    from app.core import guest_agent, libvirt_utils

    with libvirt_utils.node_conn(node) as conn:
        try:
            domain = conn.lookupByName(vm)
        except Exception:
            raise DaemonError("not_found", f"VM '{vm}' not found") from None
        if not domain.isActive():
            raise DaemonError("in_use", f"VM '{vm}' is stopped")
        ip = guest_agent.address_now(domain)
    if not ip:
        raise DaemonError("refused", f"VM '{vm}' reports no address: its guest agent must be running")
    return ip


class OpenArgs(Args):
    vm: str = Field(min_length=1, max_length=64)
    node: str | None = NODE
    sid: str = SID
    width: int = Field(1280, ge=320, le=7680)
    height: int = Field(800, ge=200, le=4320)
    port: int = Field(3389, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=256)
    domain: str = Field("", max_length=128)


@operation("rdp.open", OpenArgs, needs=on_vm("vm.console"))
def open_session(args, ctx):
    """Start the session's unit, wait for its display; {"sid", "address"}."""
    from app.core import rdp_box

    if missing := missing_tools():
        raise DaemonError(
            "unavailable", f"Remote desktop needs {rdp_box.PACKAGES} on this node (missing: {', '.join(missing)})"
        )
    ip = _guest_address(args.vm, args.node)
    cred = rdp_box.cred_path(args.sid)
    try:
        text = rdp_box.args_file(ip, args.port, *rdp_box.size(args.width, args.height), args.username, args.password,
                                 args.domain)  # fmt: skip
    except ValueError as e:
        raise DaemonError("invalid_arguments", str(e)) from None
    cred.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    try:
        r = _run(rdp_box.argv(args.sid, ip, args.width, args.height))
        if r.returncode != 0:
            raise DaemonError("failed", f"The remote desktop did not start: {r.stderr.strip()[-300:]}")
        sock = rdp_box.socket_path(args.sid)
        deadline = time.monotonic() + START_WAIT_S
        while not sock.exists():
            if time.monotonic() > deadline or not _active(args.sid):
                why = _last_words(args.sid)
                _stop(args.sid)
                raise DaemonError("failed", f"The remote desktop did not start{': ' + why if why else ''}")
            time.sleep(0.1)
    finally:
        cred.unlink(missing_ok=True)  # systemd copied it into the unit's own credentials when it started
    return {"sid": args.sid, "address": ip}


def _active(sid):
    from app.core import rdp_box

    return _run(["systemctl", "is-active", "--quiet", rdp_box.unit_name(sid)]).returncode == 0


def _last_words(sid):
    from app.core import rdp_box

    r = _run(["journalctl", "-u", rdp_box.unit_name(sid), "-n", "8", "-o", "cat", "--no-pager"])
    lines = [x for x in r.stdout.splitlines() if x.strip() and "Started" not in x and "Deactivated" not in x]
    return " / ".join(lines[-3:])[-400:]


def _stop(sid):
    from app.core import rdp_box

    _run(["systemctl", "stop", rdp_box.unit_name(sid)], timeout=30)


class SessionArgs(Args):
    sid: str = SID
    vm: str = Field(min_length=1, max_length=64)


@operation("rdp.attach", SessionArgs, needs=on_vm("vm.console"))
def attach(args, ctx):
    """A socket connected to the session's display."""
    from app.core import rdp_box

    path = rdp_box.socket_path(args.sid)
    if not path.exists():
        raise DaemonError("not_found", "This remote desktop session has ended")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
    except OSError as e:
        s.close()
        raise DaemonError("unreachable", f"The remote desktop's display cannot be reached: {e}") from e
    fd = s.detach()
    return Handover({"sid": args.sid}, [fd])


@operation("rdp.close", SessionArgs, needs=on_vm("vm.console"))
def close(args, ctx):
    """Stop the session; {"why"}: the session's last words when it ended by itself (a refused sign-in, a changed
    certificate), else empty."""
    why = "" if _active(args.sid) else _last_words(args.sid)
    _stop(args.sid)
    return {"why": why}


@operation("rdp.list", needs=ADMIN)
def list_sessions(args, ctx):
    """The session ids running on this node, and whether the tools are installed."""
    r = _run(["systemctl", "list-units", "--plain", "--no-legend", "hyperlite-rdp-*.service"])
    found = [
        line.split()[0][len("hyperlite-rdp-") : -len(".service")] for line in r.stdout.splitlines() if line.split()
    ]
    return {"sessions": found, "missing": missing_tools()}
