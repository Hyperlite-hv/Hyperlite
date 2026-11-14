"""The Terminal's box (docs/design/workbench.md, sections 4 and 6): how hyperlite-daemon starts it.

Pure functions from fixed code and the settings: nothing here comes from a request but the session id (checked) and the
account's uid (allocated by the API, checked against its range). Unit-tested property by property
(tests/test_workbench_box.py).
"""

import re

from app.core import paths

SID_RE = re.compile(r"^[a-f0-9]{16}$")
UID_MIN = (
    700000  # outside the system's and systemd's dynamic ranges; one stable uid per account (app/core/workbench.py)
)
UID_MAX = 799999
SCRIPTS = paths.CODE / "scripts" / "workbench"
RUN = paths.RUN / "workbench"  # /run/hyperlite/workbench/<sid>: the session's socket directory, bound into the box
HOMES = paths.VAR / "workbench" / "homes"  # <uid>.img
MOUNTS = paths.VAR / "workbench" / "mnt"  # <uid>: the mounted home

HIDDEN = (
    "/etc/hyperlite",
    "/var/lib/hyperlite",
    "/var/lib/libvirt",
    "/etc/libvirt",
    "/run/libvirt",
    "/run/hyperlite",
    "/var/log",
    "/var/backups",
    "/srv",
    "/mnt",
    "/media",
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/ssh",
    "/etc/corosync",
    "/var/lib/corosync",
)
SYSCALLS_DENIED = ("@privileged", "@resources", "@mount", "@swap", "@reboot", "@raw-io", "@module", "@debug", "@cpu-emulation", "@obsolete")  # fmt: skip

DEFAULT_LIMITS = {"memory_mb": 512, "cpu_percent": 50, "tasks": 128, "max_hours": 8}


def unit_name(sid):
    _check(sid)
    return f"hyperlite-workbench-{sid}.service"


def relay_unit_name(sid):
    _check(sid)
    return f"hyperlite-workbench-{sid}-relay.service"


def _check(sid, uid=None):
    if not SID_RE.match(sid):
        raise ValueError("A Terminal session id is 16 hexadecimal digits")
    if uid is not None and not (UID_MIN <= int(uid) <= UID_MAX):
        raise ValueError("A Terminal uid is in its own range")


def box_properties(sid, uid, limits):
    """The transient unit's properties, each a `Name=value` string (section 6)."""
    _check(sid, uid)
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    run = RUN / sid
    props = [
        f"User={uid}",
        f"Group={uid}",
        "PrivateUsers=yes",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "ProtectSystem=strict",
        "ProtectHome=tmpfs",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "DevicePolicy=closed",
        "PrivateNetwork=yes",
        "PrivateIPC=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
        "ProtectProc=invisible",
        "ProcSubset=pid",
        "RestrictNamespaces=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "RestrictSUIDSGID=yes",
        "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service",
        "SystemCallFilter=~" + " ".join(SYSCALLS_DENIED),
        # A refused call fails with EPERM instead of killing the process: tmux and the shells it starts try calls the
        # filter refuses (found in the test VM: every pane died of SIGSYS) and do without them.
        "SystemCallErrorNumber=EPERM",
        "UMask=0077",
        "KeyringMode=private",
        "OOMScoreAdjust=500",
        # A process past the memory limit is killed alone; the session goes on (systemd stops the unit by default).
        "OOMPolicy=continue",
        f"MemoryMax={int(lim['memory_mb'])}M",
        "MemorySwapMax=0",
        f"CPUQuota={int(lim['cpu_percent'])}%",
        f"TasksMax={int(lim['tasks'])}",
        "IOWeight=50",
        f"RuntimeMaxSec={int(lim['max_hours']) * 3600}",
        "InaccessiblePaths=" + " ".join(f"-{p}" for p in HIDDEN),
        f"BindPaths={MOUNTS / str(uid)}:/home/workbench {run}:/run/workbench",
        "WorkingDirectory=/home/workbench",
        "Environment=HOME=/home/workbench",
        "Environment=SHELL=/bin/bash",
        "Environment=TERM=xterm-256color",
        "Environment=LANG=C.UTF-8",
        "Environment=HYPERLITE_CONFIG=/run/workbench/hyperlite.json",
        f"Environment=HL_TERMINAL_SID={sid}",
        f"Environment=PATH=/home/workbench/.local/bin:{paths.CODE}/cli/dist/linux-amd64:/usr/local/bin:/usr/bin:/bin",
    ]
    return props


def box_argv(sid, uid, limits):
    """The systemd-run command that starts the box."""
    argv = ["systemd-run", "--quiet", "--collect", f"--unit={unit_name(sid)}", "--description=Hyperlite Terminal"]
    argv += [f"--property={p}" for p in box_properties(sid, uid, limits)]
    return [*argv, "/bin/bash", str(SCRIPTS / "box.sh")]


def relay_argv(sid, uid):
    """The relay outside the box: the session's socket to the API, and nowhere else (it is ours, not the person's)."""
    _check(sid, uid)
    run = RUN / sid
    props = [
        f"User={uid}",
        f"Group={uid}",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET",
        "IPAddressDeny=any",
        "IPAddressAllow=127.0.0.1",
        f"ReadWritePaths={run}",
        f"BindsTo={unit_name(sid)}",
        f"After={unit_name(sid)}",
        "MemoryMax=64M",
    ]
    return [
        "systemd-run", "--quiet", "--collect", f"--unit={relay_unit_name(sid)}", "--description=Hyperlite Terminal relay",
        *[f"--property={p}" for p in props],
        "/usr/bin/python3", "-I", str(SCRIPTS / "relay.py"), "unix-to-tcp", str(run / "api.sock"), "127.0.0.1", "8000",
    ]  # fmt: skip


def attach_argv(sid, uid):
    """The tmux client the daemon runs in a terminal, as the session's uid, outside the box: the shells it shows run
    inside, in the tmux server's box. Attach only: a client that made a session would start a server, and its shells,
    outside the box when the box's server is gone."""
    _check(sid, uid)
    return [
        "setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "--inh-caps=-all", "--no-new-privs", "--",
        "/usr/bin/tmux", "-S", str(RUN / sid / "tmux.sock"), "attach-session", "-t", "main",
    ]  # fmt: skip
