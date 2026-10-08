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
import socket
import subprocess
import time
from pathlib import Path

ETC_HOSTS = Path("/etc/hosts")
ETC_HOSTNAME = Path("/etc/hostname")
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


# Packages whose new version is only used after a reboot: Debian then writes /run/reboot-required. Known before the
# update from the names alone, so the update dialog can say it beforehand (docs/design/updates-1.0.md).
REBOOT_PATTERNS = (
    re.compile(r"^linux-image-"),
    re.compile(r"^linux-firmware"),
    re.compile(r"^firmware-"),
    re.compile(r"^(intel|amd64)-microcode$"),
    re.compile(r"^libc6$"),
    re.compile(r"^systemd$"),
    re.compile(r"^dbus$"),
)


def needs_reboot(packages):
    """The names, among `packages`, that need a reboot to be used."""
    return sorted(n for n in packages if any(p.match(n) for p in REBOOT_PATTERNS))


# ---- Automatic security updates (docs/design/updates-1.0.md) ----
# Debian's own unattended-upgrades, limited to the security archive, every night at a time chosen per node, never
# rebooting by itself; Hyperlite is never updated this way, only announced. Our settings come after Debian's defaults
# (APT reads apt.conf.d in order, a later value wins).
AUTO_CONF = Path("/etc/apt/apt.conf.d/52hyperlite-security-updates")
AUTO_TIMERS = {
    "apt-daily.timer": Path("/etc/systemd/system/apt-daily.timer.d/hyperlite.conf"),
    "apt-daily-upgrade.timer": Path("/etc/systemd/system/apt-daily-upgrade.timer.d/hyperlite.conf"),
}
AUTO_DEFAULT_TIME = "03:30"
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_ON_CALENDAR_RE = re.compile(r"OnCalendar=\*-\*-\* (\d\d:\d\d)")


def auto_updates():
    """{"actif", "heure", "installe"}: the night run of the security updates on this node."""
    text = AUTO_CONF.read_text() if AUTO_CONF.exists() else ""
    timer = AUTO_TIMERS["apt-daily-upgrade.timer"]
    m = _ON_CALENDAR_RE.search(timer.read_text()) if timer.exists() else None
    return {
        "actif": 'APT::Periodic::Unattended-Upgrade "1";' in text,
        "heure": m.group(1) if m else AUTO_DEFAULT_TIME,
        "installe": shutil.which("unattended-upgrade") is not None,
    }


def set_auto_updates(active, at):
    if not TIME_RE.match(at or ""):
        raise SettingError("Invalid time (expected HH:MM)")
    flag = "1" if active else "0"
    _write(
        AUTO_CONF,
        "// Written by Hyperlite (Node > Updates). Security updates only, never a reboot, never Hyperlite itself.\n"
        f'APT::Periodic::Update-Package-Lists "{flag}";\n'
        f'APT::Periodic::Unattended-Upgrade "{flag}";\n'
        # Debian's own file lists its stable point releases too, and APT adds the lists up: cleared first.
        "#clear Unattended-Upgrade::Origins-Pattern;\n"
        "Unattended-Upgrade::Origins-Pattern {\n"
        '  "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";\n'
        '  "origin=Debian,codename=${distro_codename},label=Debian-Security";\n'
        "};\n"
        'Unattended-Upgrade::Package-Blacklist { "hyperlite"; };\n'
        'Unattended-Upgrade::Automatic-Reboot "false";\n',
    )
    hh, mm = (int(x) for x in at.split(":"))
    # The package lists are read 30 minutes before the upgrade, so it installs what was published that night.
    before = (hh * 60 + mm - 30) % 1440
    for timer, when in (("apt-daily.timer", f"{before // 60:02d}:{before % 60:02d}"), ("apt-daily-upgrade.timer", at)):
        _write(
            AUTO_TIMERS[timer],
            f"[Timer]\nOnCalendar=\nOnCalendar=*-*-* {when}\nRandomizedDelaySec=0\nPersistent=true\n",
        )
    _run(["systemctl", "daemon-reload"])
    for timer in AUTO_TIMERS:
        _run(["systemctl", "enable", "--now", timer])
    return auto_updates()


def ensure_auto_updates_default():
    """On a node that never chose: the night run on, at AUTO_DEFAULT_TIME (the package's first installation)."""
    if not AUTO_CONF.exists():
        set_auto_updates(True, AUTO_DEFAULT_TIME)


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


def upgrade_command(packages, upgradable=None):
    """apt command upgrading the requested packages. The names in the command are apt's own (the packages it lists
    as upgradable right now), never the request's strings: a name apt does not list is refused, and `hyperlite`
    is left to its own update page."""
    listed = [p["nom"] for p in (upgradable if upgradable is not None else updates()["paquets"])]
    requested = set(packages)
    unknown = sorted(requested - set(listed) - {SELF_PACKAGE})
    if unknown:
        raise SettingError(f"Not upgradable on this node: {', '.join(unknown)[:300]}")
    names = [n for n in listed if n in requested and n != SELF_PACKAGE and PKG_RE.match(n)]
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


def run_upgrade(cmd, log):
    """Run an upgrade command made by upgrade_command, passing every output line to `log`. Returns the exit code."""
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


# ---- Host name ----
# The name Hyperlite shows for this node is the one libvirt reports: the host name, completed by /etc/hosts when it
# has no domain (Debian keeps "hyperlite" as the host name and "127.0.1.1 hyperlite.home hyperlite" in /etc/hosts).
# Renaming the node sets both, so the name shown is exactly the one given.


def hosts_with_name(text, old_names, fqdn, short):
    """/etc/hosts with this host's 127.0.1.1 line naming it `fqdn` (None: a name without domain) and `short`, and
    the old names replaced on the other lines that list them. Comments and other hosts are kept as they are."""
    own = f"127.0.1.1\t{fqdn}\t{short}\n" if fqdn else f"127.0.1.1\t{short}\n"
    new_for = {name: (fqdn or short) if "." in name else short for name in old_names if name and name != "localhost"}
    out, placed = [], False
    for line in text.splitlines(keepends=True):
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] == "127.0.1.1":
            if not placed:
                out.append(own)
                placed = True
            continue
        if len(fields) > 1 and any(f in new_for for f in fields[1:]):
            names = []
            for f in fields[1:]:
                f = new_for.get(f, f)
                if f not in names:
                    names.append(f)
            comment = f" #{line.split('#', 1)[1].rstrip()}" if "#" in line else ""
            out.append(f"{fields[0]}\t{chr(9).join(names)}{comment}\n")
            continue
        out.append(line)
    if not placed:
        at = next((i + 1 for i, line in enumerate(out) if line.split()[:1] == ["127.0.0.1"]), 0)
        out.insert(at, own)
    return "".join(out)


def set_hostname(name, current=None):
    """Rename this host: `name` with a domain ("pve1.lan") is its full name and its first label its host name;
    without one, both. Returns {"nom", "ancien"}."""
    name = (name or "").strip().rstrip(".").lower()
    if not HOST_RE.match(name) or name.split(".")[0] in ("localhost", "local") or name.replace(".", "").isdigit():
        raise SettingError(
            "Invalid host name: letters, digits and hyphens, dot-separated parts of 1 to 63 characters "
            "(for example pve1 or pve1.home)"
        )
    short = name.split(".")[0]
    fqdn = name if "." in name else None
    old = current or socket.getfqdn()
    old_names = {old, old.split(".")[0], socket.gethostname()}
    # What hostnamectl set-hostname does, without running a command with a name the user typed: the static name
    # in /etc/hostname (read again by systemd-hostnamed) and the kernel's, which libvirt reports at once.
    try:
        socket.sethostname(short)
    except OSError as e:
        raise SettingError(f"The host name could not be set: {e.strerror}") from e
    _write(ETC_HOSTNAME, short + "\n")
    try:
        text = ETC_HOSTS.read_text()
    except OSError:
        text = "127.0.0.1\tlocalhost\n"
    _write(ETC_HOSTS, hosts_with_name(text, old_names, fqdn, short))
    return {"nom": name, "ancien": old}


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
        # The zone passed to timedatectl is the system's own spelling of it, never the request's string.
        known = {z: z for z in timezones()} if TZ_RE.match(timezone or "") else {}
        zone = known.get(timezone)
        if zone is None:
            raise SettingError(f"Unknown time zone: {timezone}")
        r = _run(["timedatectl", "set-timezone", zone])
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


# ---- Power ----

POWER_ACTIONS = {"reboot": "reboot", "poweroff": "poweroff"}
POWER_DELAY_S = 5


def running_guests():
    """Running VMs and containers of this node, as (kind, name)."""
    from app.core.libvirt_utils import open_conn, open_lxc_conn

    found = []
    for kind, opener in (("vm", open_conn), ("conteneur", open_lxc_conn)):
        try:
            conn = opener()
        except Exception:  # noqa: S112 -- no LXC driver on this node: it simply has no containers
            continue
        try:
            found += [(kind, d.name()) for d in conn.listAllDomains(1)]  # VIR_CONNECT_LIST_DOMAINS_ACTIVE
        finally:
            conn.close()
    return found


def stop_guests(guests, log, timeout_s=180, poll_s=3):
    """Ask each running guest to shut down, then wait. Returns the names still running at the end."""
    from app.core.libvirt_utils import open_conn, open_lxc_conn

    conns = {"vm": open_conn(), "conteneur": None}
    try:
        try:
            conns["conteneur"] = open_lxc_conn()
        except Exception:
            conns["conteneur"] = None
        for kind, name in guests:
            try:
                conns[kind].lookupByName(name).shutdown()
                log(f"Shutdown requested: {name}")
            except Exception as e:
                log(f"Shutdown request failed for {name}: {e}")
        waited = 0
        while True:
            left = []
            for kind, name in guests:
                try:
                    if conns[kind].lookupByName(name).isActive():
                        left.append(name)
                except Exception:  # noqa: S112 -- gone (transient domain): it is stopped
                    continue
            if not left or waited >= timeout_s:
                return left
            time.sleep(poll_s)
            waited += poll_s
    finally:
        for c in conns.values():
            if c is not None:
                c.close()


def schedule_power(action):
    """Reboot or power off the node a few seconds from now, from outside the service (a transient timer), so the
    answer and the task's end are recorded first."""
    if action not in POWER_ACTIONS:
        raise SettingError("action must be reboot or poweroff")
    if not (shutil.which("systemd-run") and Path("/run/systemd/system").is_dir()):
        raise SettingError("This node does not run systemd: reboot or shut it down from its console")
    r = _run(
        ["systemd-run", "--quiet", "--collect", f"--on-active={POWER_DELAY_S}", "systemctl", POWER_ACTIONS[action]]
    )
    if r.returncode != 0:
        raise SettingError(f"The {action} could not be scheduled: {r.stderr.strip()[-300:]}")
