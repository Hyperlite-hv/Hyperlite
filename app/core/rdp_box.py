"""The remote desktop's box (docs/design/console-2.md, section 8): how hyperlite-daemon starts one session.

Pure functions from fixed code: nothing here comes from a request but the session id (checked), the guest's address
(read by the daemon from the VM itself), a size (bounded) and the account the person types, written to the unit's
credential file only (`args_file`), never on a command line. Unit-tested property by property (tests/test_rdp.py).
"""

import ipaddress
import re

from app.core import paths

SID_RE = re.compile(r"^[a-f0-9]{16}$")
SCRIPT = paths.CODE / "scripts" / "rdp" / "session.sh"
CRED_DIR = paths.RUN  # /run/hyperlite: root's own, systemd copies the file into the unit's credentials at its start
MAX_HOURS = 8
TOOLS = ("Xvnc", "xfreerdp3")
PACKAGES = "tigervnc-standalone-server freerdp3-x11"


def _check_sid(sid):
    if not SID_RE.match(sid or ""):
        raise ValueError("A remote desktop session id is 16 hexadecimal digits")


def unit_name(sid):
    _check_sid(sid)
    return f"hyperlite-rdp-{sid}.service"


def runtime_dir(sid):
    _check_sid(sid)
    return paths.RUN.parent / f"hyperlite-rdp-{sid}"


def socket_path(sid):
    return runtime_dir(sid) / "vnc.sock"


def cred_path(sid):
    _check_sid(sid)
    return CRED_DIR / f"rdp-{sid}.args"


def _line(value, what, limit=256):
    value = str(value or "")
    if len(value) > limit or any(c in value for c in "\r\n\x00"):
        raise ValueError(f"The {what} is at most {limit} characters, on one line")
    return value


def args_file(host, port, width, height, username, password, domain=""):
    """FreeRDP's arguments, one per line (/args-from): the guest, the size, the account, the clipboard, and the guest's
    certificate trusted on first use and remembered (a changed one stops the connection)."""
    ipaddress.ip_address(host)
    if not 1 <= int(port) <= 65535:
        raise ValueError("A port from 1 to 65535")
    lines = [
        f"/v:{host}:{int(port)}",
        f"/size:{int(width)}x{int(height)}",
        "/f",
        "/cert:tofu",
        "+clipboard",
        "/log-level:WARN",
        "-grab-keyboard",
        "/auto-reconnect",
        f"/u:{_line(username, 'account name', 128)}",
        f"/p:{_line(password, 'password')}",
    ]
    if domain:
        lines.append(f"/d:{_line(domain, 'domain', 128)}")
    return "\n".join(lines) + "\n"


def size(width, height):
    """A size the X server accepts: within 640x480 and 3840x2160, even numbers."""
    w = max(640, min(3840, int(width))) // 2 * 2
    h = max(480, min(2160, int(height))) // 2 * 2
    return w, h


def properties(sid, host, width, height):
    """The transient unit's properties, each a `Name=value` string."""
    _check_sid(sid)
    ip = ipaddress.ip_address(host)
    w, h = size(width, height)
    return [
        "DynamicUser=yes",
        f"RuntimeDirectory=hyperlite-rdp-{sid}",
        "RuntimeDirectoryMode=0700",
        "StateDirectory=hyperlite-rdp",  # FreeRDP's remembered certificates, kept across sessions
        "Environment=HOME=/var/lib/hyperlite-rdp",
        f"Environment=RDP_W={w}",
        f"Environment=RDP_H={h}",
        f"LoadCredential=rdp:{cred_path(sid)}",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "TemporaryFileSystem=/var/lib/xkb",  # where the X server compiles its keymaps
        "PrivateDevices=yes",
        "PrivateIPC=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
        "ProtectProc=invisible",
        "RestrictNamespaces=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "RestrictSUIDSGID=yes",
        "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service",
        "SystemCallErrorNumber=EPERM",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        # The guest's address only: the session reaches nothing else on the network.
        "IPAddressDeny=any",
        f"IPAddressAllow={ip}",
        "MemoryMax=768M",
        "TasksMax=128",
        "CPUQuota=100%",
        f"RuntimeMaxSec={MAX_HOURS * 3600}",
        "UMask=0077",
    ]


def argv(sid, host, width, height):
    """The systemd-run command that starts the session."""
    return [
        "systemd-run", "--quiet", "--collect", f"--unit={unit_name(sid)}", "--description=Hyperlite remote desktop",
        *[f"--property={p}" for p in properties(sid, host, width, height)],
        "/bin/bash", str(SCRIPT),
    ]  # fmt: skip
