"""System settings of the machine running this Hyperlite: package updates, DNS resolvers, time, remote syslog.

Everything here runs as root on the host, so every value that reaches a file or a command is checked against a
strict pattern first, commands take argument lists (never a shell), and files are replaced atomically.

Updates: the upgrade names the packages it upgrades (the ones apt lists as upgradable) and leaves out `hyperlite`,
which updates from its own page: its maintainer script restarts this service, which must not happen under an apt
run started by the service. The run goes through `systemd-run --wait --pipe` so that a restart of the service
while it runs does not kill dpkg halfway.

DNS: written where the system reads it: a systemd-resolved drop-in when resolv.conf points to systemd-resolved,
/etc/resolv.conf itself when it is a plain file nobody regenerates. A file written by a DHCP client or resolvconf
would be overwritten at the next lease: that case is refused with the reason rather than accepted and lost.
"""

import ipaddress
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

RESOLV_CONF = Path("/etc/resolv.conf")
RESOLVED_DROPIN = Path("/etc/systemd/resolved.conf.d/hyperlite.conf")
TIMESYNCD_DROPIN = Path("/etc/systemd/timesyncd.conf.d/hyperlite.conf")
RSYSLOG_DROPIN = Path("/etc/rsyslog.d/90-hyperlite-remote.conf")
REBOOT_REQUIRED = Path("/var/run/reboot-required")
REBOOT_PKGS = Path("/var/run/reboot-required.pkgs")
APT_LISTS = Path("/var/lib/apt/lists")

HOST_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
TZ_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
SELF_PACKAGE = "hyperlite"


class SettingError(ValueError):
    """A value or an action refused, with a message for the user."""


def _run(argv, timeout=60, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)


def _write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hyperlite-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _hosts(values, what, allow_names=True):
    out = []
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        try:
            out.append(str(ipaddress.ip_address(v)))
            continue
        except ValueError:
            pass
        if allow_names and HOST_RE.match(v):
            out.append(v)
            continue
        raise SettingError(f"Invalid {what}: {v}")
    return out


# ---- Updates ----

_UPGRADABLE_RE = re.compile(r"^([a-z0-9][a-z0-9+.-]+)/(\S+)\s+(\S+)\s+(\S+)\s+\[upgradable from: ([^\]]+)\]")


def parse_upgradable(text):
    packages = []
    for line in text.splitlines():
        m = _UPGRADABLE_RE.match(line.strip())
        if m:
            name, suites, version, _arch, old = m.groups()
            packages.append(
                {
                    "nom": name,
                    "version": version,
                    "depuis": old,
                    "securite": "-security" in suites or "/security" in suites,
                }
            )
    return packages


def updates(refresh=False):
    if shutil.which("apt-get") is None:
        return {
            "disponible": False,
            "paquets": [],
            "redemarrage_requis": False,
            "paquets_redemarrage": [],
            "actualise_le": None,
        }
    if refresh:
        r = _run(["apt-get", "update"], timeout=180)
        if r.returncode != 0:
            raise SettingError(f"apt-get update failed: {(r.stderr or r.stdout).strip()[-400:]}")
    listed = _run(["apt", "list", "--upgradable"], timeout=60, env={**os.environ, "LC_ALL": "C"})
    reboot_pkgs = []
    if REBOOT_PKGS.exists():
        reboot_pkgs = sorted({p.strip() for p in REBOOT_PKGS.read_text().splitlines() if p.strip()})
    stamp = None
    try:
        stamp = int(APT_LISTS.stat().st_mtime)
    except OSError:
        stamp = None
    return {
        "disponible": True,
        "paquets": parse_upgradable(listed.stdout),
        "redemarrage_requis": REBOOT_REQUIRED.exists(),
        "paquets_redemarrage": reboot_pkgs,
        "actualise_le": stamp,
    }


def upgrade_command(packages):
    names = [p for p in packages if p != SELF_PACKAGE]
    bad = [p for p in names if not PKG_RE.match(p)]
    if bad:
        raise SettingError(f"Invalid package names: {', '.join(bad)}")
    if not names:
        raise SettingError("Nothing to upgrade")
    apt = [
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "install",
        "--only-upgrade",
        "-y",
        "-o",
        "Dpkg::Options::=--force-confdef",
        "-o",
        "Dpkg::Options::=--force-confold",
        *names,
    ]
    # systemd-run needs systemd as the running init (the sd_booted() test), not only the binary.
    if shutil.which("systemd-run") and Path("/run/systemd/system").is_dir():
        unit = f"hyperlite-host-upgrade-{int(time.time())}"
        return ["systemd-run", "--quiet", "--collect", "--wait", "--pipe", f"--unit={unit}", *apt]
    return apt


def run_upgrade(packages, log):
    """Upgrade the packages, passing every output line to `log`. Returns the exit code."""
    cmd = upgrade_command(packages)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
    return proc.wait()


# ---- DNS ----


def _resolv_manager():
    if RESOLV_CONF.is_symlink():
        target = os.readlink(RESOLV_CONF)
        if "systemd/resolve" in target:
            return "systemd-resolved"
        return "lien"
    try:
        head = RESOLV_CONF.read_text()[:2000].lower()
    except OSError:
        return "absent"
    if "generated by" in head or "resolvconf" in head or "networkmanager" in head or "dhclient" in head:
        return "automatique"
    return "fichier"


def _parse_resolv(text):
    servers, search = [], []
    for line in text.splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
        elif len(parts) >= 2 and parts[0] in ("search", "domain"):
            search = parts[1:]
    return servers, search


def dns():
    manager = _resolv_manager()
    servers, search = [], []
    if manager == "systemd-resolved" and RESOLVED_DROPIN.exists():
        for line in RESOLVED_DROPIN.read_text().splitlines():
            if line.startswith("DNS="):
                servers = line[4:].split()
            elif line.startswith("Domains="):
                search = line[8:].split()
    else:
        try:
            servers, search = _parse_resolv(RESOLV_CONF.read_text())
        except OSError:
            servers, search = [], []
    return {
        "gere_par": manager,
        "modifiable": manager in ("systemd-resolved", "fichier"),
        "serveurs": servers,
        "recherche": search,
    }


def set_dns(servers, search):
    servers = _hosts(servers, "DNS server", allow_names=False)
    if not servers:
        raise SettingError("At least one DNS server is needed")
    if len(servers) > 3:
        raise SettingError("At most 3 DNS servers (the resolver ignores the others)")
    search = [s.strip() for s in search if s and s.strip()]
    bad = [s for s in search if not HOST_RE.match(s)]
    if bad:
        raise SettingError(f"Invalid search domain: {', '.join(bad)}")
    manager = _resolv_manager()
    if manager == "systemd-resolved":
        text = "# Written by Hyperlite (node DNS settings).\n[Resolve]\n" + f"DNS={' '.join(servers)}\n"
        if search:
            text += f"Domains={' '.join(search)}\n"
        _write(RESOLVED_DROPIN, text)
        r = _run(["systemctl", "restart", "systemd-resolved"])
        if r.returncode != 0:
            raise SettingError(f"systemd-resolved did not restart: {r.stderr.strip()[-300:]}")
    elif manager == "fichier":
        text = "# Written by Hyperlite (node DNS settings).\n" + "".join(f"nameserver {s}\n" for s in servers)
        if search:
            text += f"search {' '.join(search)}\n"
        _write(RESOLV_CONF, text)
    else:
        raise SettingError(
            "/etc/resolv.conf is written by the network configuration (DHCP client or resolvconf): "
            "set the DNS servers there, otherwise the next lease would replace them"
        )
    return dns()


# ---- Time ----


def _timedatectl_show():
    r = _run(["timedatectl", "show"])
    if r.returncode != 0:
        raise SettingError(f"timedatectl is not available: {r.stderr.strip()[-200:]}")
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def time_settings():
    show = _timedatectl_show()
    servers = []
    if TIMESYNCD_DROPIN.exists():
        for line in TIMESYNCD_DROPIN.read_text().splitlines():
            if line.startswith("NTP="):
                servers = line[4:].split()
    return {
        "fuseau": show.get("Timezone"),
        "ntp": show.get("NTP") == "yes",
        "synchronise": show.get("NTPSynchronized") == "yes",
        "timesyncd": Path("/lib/systemd/system/systemd-timesyncd.service").exists()
        or Path("/usr/lib/systemd/system/systemd-timesyncd.service").exists(),
        "serveurs_ntp": servers,
    }


def timezones():
    r = _run(["timedatectl", "list-timezones"])
    return [z for z in r.stdout.split() if TZ_RE.match(z)]


def set_time(timezone, ntp, ntp_servers):
    if timezone is not None:
        if not TZ_RE.match(timezone) or timezone not in timezones():
            raise SettingError(f"Unknown time zone: {timezone}")
        r = _run(["timedatectl", "set-timezone", timezone])
        if r.returncode != 0:
            raise SettingError(f"The time zone was not changed: {r.stderr.strip()[-300:]}")
    if ntp_servers is not None:
        servers = _hosts(ntp_servers, "NTP server")
        if servers:
            _write(
                TIMESYNCD_DROPIN,
                "# Written by Hyperlite (node time settings).\n[Time]\n" + f"NTP={' '.join(servers)}\n",
            )
        elif TIMESYNCD_DROPIN.exists():
            TIMESYNCD_DROPIN.unlink()
        _run(["systemctl", "try-restart", "systemd-timesyncd"])
    if ntp is not None:
        r = _run(["timedatectl", "set-ntp", "true" if ntp else "false"])
        if r.returncode != 0:
            raise SettingError(f"Network time was not changed: {r.stderr.strip()[-300:]}")
    return time_settings()


# ---- Remote syslog ----

_RSYSLOG_RE = re.compile(r"^\*\.\* (@@?)(?:\[([^\]]+)\]|([^:\s]+)):(\d+)$")


def syslog():
    available = shutil.which("rsyslogd") is not None
    target = None
    if RSYSLOG_DROPIN.exists():
        for line in RSYSLOG_DROPIN.read_text().splitlines():
            m = _RSYSLOG_RE.match(line.strip())
            if m:
                target = {
                    "hote": m.group(2) or m.group(3),
                    "port": int(m.group(4)),
                    "protocole": "tcp" if m.group(1) == "@@" else "udp",
                }
    return {"disponible": available, "cible": target}


def set_syslog(host, port, protocol):
    if shutil.which("rsyslogd") is None:
        raise SettingError("rsyslog is not installed on this node: apt install rsyslog")
    if host is None or host == "":
        if RSYSLOG_DROPIN.exists():
            RSYSLOG_DROPIN.unlink()
    else:
        (host,) = _hosts([host], "syslog server")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise SettingError("The port must be between 1 and 65535")
        if protocol not in ("udp", "tcp"):
            raise SettingError("The protocol must be udp or tcp")
        shown = f"[{host}]" if ":" in host else host
        prefix = "@@" if protocol == "tcp" else "@"
        _write(RSYSLOG_DROPIN, f"# Written by Hyperlite (remote syslog).\n*.* {prefix}{shown}:{port}\n")
    r = _run(["systemctl", "restart", "rsyslog"])
    if r.returncode != 0:
        raise SettingError(f"rsyslog did not restart: {r.stderr.strip()[-300:]}")
    return syslog()
