#!/usr/bin/env python3
"""Hyperlite preflight check.

Standard library only: it must run on a BARE machine, before the venv exists.
Each check returns one of these statuses:
  ok         -- satisfied
  warning    -- usable but degraded / worth watching
  disabled   -- one specific feature will be unavailable (the rest works)
  blocking   -- the installation must not continue

Exit code: 1 if there is at least one `blocking` check, otherwise 0.

Usage:
  python3 preflight.py [--json] [--python /root/hyperlite/venv/bin/python3]
                       [--requirements requirements.txt] [--only system|python]
                       [--offline]

"""

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OK, WARNING, DISABLED, BLOCKING = "ok", "warning", "disabled", "blocking"

MIN_PYTHON = (3, 11)
MIN_RAM_MB_BLOCKING = 1024
MIN_RAM_MB_WARNING = 4096
MIN_APP_DISK_GB = 2
MIN_VM_DISK_GB_WARNING = 20
APP_DIR = Path(os.environ.get("HYPERLITE_APP_DIR", "/root/hyperlite"))
VM_IMAGES_DIR = Path("/var/lib/libvirt/images")

# (importable module, pip distribution, severity if missing, feature)
# severity: BLOCKING = the application does not start / a route breaks at import
# time; DISABLED = only the named feature is lost.
PYTHON_MODULES = [
    ("fastapi", "fastapi", BLOCKING, "API web"),
    ("uvicorn", "uvicorn", BLOCKING, "serveur HTTP"),
    ("libvirt", "libvirt-python", BLOCKING, "pilotage libvirt/QEMU"),
    ("multipart", "python-multipart", BLOCKING, "file uploads (ISO, disks)"),
    ("jwt", "PyJWT", BLOCKING, "jetons de session (JWT)"),
    ("bcrypt", "bcrypt", BLOCKING, "password hashing"),
    ("cryptography", "cryptography", BLOCKING, "secret encryption, SSH"),
    ("asyncssh", "asyncssh", BLOCKING, "terminaux SSH, cluster"),
    ("pyotp", "pyotp", DISABLED, "authentification 2FA (TOTP)"),
    ("qrcode", "qrcode", DISABLED, "QR code d'activation 2FA"),
]
# Either one is enough for WebSockets (uvicorn[standard] brings websockets).
WEBSOCKET_MODULES = ("websockets", "wsproto")
WEBSOCKET_FEATURE = "host shell, VM consoles (noVNC/terminal), container terminals"


def _c(id_, statut, message, feature=None, action=None):
    d = {"id": id_, "statut": statut, "message": message}
    if feature:
        d["fonctionnalite"] = feature
    if action:
        d["action"] = action
    return d


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _mem_total_mb():
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def _existing_parent(path):
    p = Path(path)
    while p != p.parent:
        try:
            if p.exists():
                break
        except OSError:  # e.g. a parent that is not accessible to this user
            pass
        p = p.parent
    return p


def _free_gb(path):
    return shutil.disk_usage(_existing_parent(path)).free / 1024**3


def _secure_boot():
    if not Path("/sys/firmware/efi").exists():
        return "bios_legacy"
    for entry in Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"):
        try:
            data = entry.read_bytes()
            if len(data) >= 5:
                return "active" if data[-1] == 1 else "inactive"
        except OSError:
            continue
    return "uefi_inconnu"


def _zfs_module_loaded():
    return any(line.split()[0] == "zfs" for line in _read("/proc/modules").splitlines() if line.strip())


def _in_chroot():
    try:
        return os.stat("/").st_ino != os.stat("/proc/1/root/.").st_ino
    except OSError:
        return False


def _kvm_device_present():
    return Path("/dev/kvm").exists()


def _port_in_use(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _service_active(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _apt_source_url():
    for f in list(Path("/etc/apt/sources.list.d").glob("hyperlite*")):
        for line in _read(f).splitlines():
            m = re.match(r"\s*deb\s+(?:\[[^\]]*\]\s+)?(\S+)", line)
            if m:
                return m.group(1)
    return None


def check_system(offline=False):
    out = []
    machine = platform.machine()
    if machine in ("x86_64", "AMD64"):
        out.append(_c("architecture", OK, f"Architecture {machine}"))
    else:
        out.append(_c("architecture", BLOCKING, f"Architecture {machine} is not supported (the package is amd64 only)"))

    if os.geteuid() == 0:
        out.append(_c("root", OK, "Running as root"))
    else:
        out.append(_c("root", BLOCKING, "Must be run as root", action="run again with sudo"))

    if sys.version_info >= MIN_PYTHON:
        out.append(_c("python", OK, f"Python {platform.python_version()}"))
    else:
        out.append(
            _c("python", BLOCKING, f"Python {platform.python_version()} < {MIN_PYTHON[0]}.{MIN_PYTHON[1]} required")
        )

    osr = dict(line.split("=", 1) for line in _read("/etc/os-release").splitlines() if "=" in line)
    distro = osr.get("PRETTY_NAME", "?").strip('"')
    family = (osr.get("ID", "") + " " + osr.get("ID_LIKE", "")).lower()
    if shutil.which("apt-get") and ("debian" in family or "ubuntu" in family):
        out.append(_c("distribution", OK, f"{distro} (apt available)"))
    else:
        out.append(
            _c("distribution", BLOCKING, f"{distro}: a Debian/apt family system is required for the hyperlite package")
        )

    if Path("/run/systemd/system").exists():
        out.append(_c("systemd", OK, "systemd actif"))
    elif _in_chroot() and shutil.which("systemctl"):
        out.append(
            _c("systemd", OK, "installation chroot: systemd is present, service start is deferred to the first boot")
        )
    else:
        out.append(_c("systemd", BLOCKING, "systemd is missing: the hyperlite.service unit cannot run"))

    missing = [b for b in ("openssl", "ssh-keygen", "chpasswd") if not shutil.which(b)]
    if missing:
        out.append(
            _c(
                "outils_installation",
                BLOCKING,
                "Tools required by the installation are missing: " + ", ".join(missing),
                action="apt-get install openssl openssh-client passwd",
            )
        )
    else:
        out.append(_c("outils_installation", OK, "openssl, ssh-keygen, chpasswd are present"))

    cpuinfo = _read("/proc/cpuinfo")
    flags = set()
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            flags = set(line.split(":", 1)[1].split())
            break
    has_virt = bool(flags & {"vmx", "svm"})
    has_kvm = _kvm_device_present()
    feat = "KVM virtual machines (LXC containers remain available)"
    if has_virt and has_kvm:
        out.append(_c("virtualisation_materielle", OK, "Hardware virtualization and /dev/kvm are present"))
    elif has_virt:
        out.append(
            _c(
                "virtualisation_materielle",
                DISABLED,
                "CPU is capable but /dev/kvm is missing (kvm module not loaded or disabled in the BIOS)",
                feature=feat,
                action="modprobe kvm_intel / kvm_amd, check the BIOS",
            )
        )
    elif "hypervisor" in flags:
        out.append(
            _c(
                "virtualisation_materielle",
                DISABLED,
                "Virtual machine without nested virtualization exposed",
                feature=feat,
                action="enable nested virtualization on the parent host",
            )
        )
    else:
        out.append(
            _c(
                "virtualisation_materielle",
                DISABLED,
                "Extensions vmx/svm absentes du CPU",
                feature=feat,
                action="enable VT-x/AMD-V in the BIOS",
            )
        )

    ram = _mem_total_mb()
    if ram is None:
        out.append(_c("memoire", WARNING, "Total memory could not be read"))
    elif ram < MIN_RAM_MB_BLOCKING:
        out.append(_c("memoire", BLOCKING, f"{ram} MB of RAM: minimum {MIN_RAM_MB_BLOCKING} MB"))
    elif ram < MIN_RAM_MB_WARNING:
        out.append(
            _c(
                "memoire",
                WARNING,
                f"{ram} MB of RAM: usable, but little headroom for VMs (recommended {MIN_RAM_MB_WARNING} MB)",
            )
        )
    else:
        out.append(_c("memoire", OK, f"{ram} MB of RAM"))

    app_free = _free_gb(APP_DIR)
    if app_free < MIN_APP_DISK_GB:
        out.append(
            _c(
                "disque_application",
                BLOCKING,
                f"{app_free:.1f} GB free under {APP_DIR}: minimum {MIN_APP_DISK_GB} GB",
            )
        )
    else:
        out.append(_c("disque_application", OK, f"{app_free:.1f} GB free under {APP_DIR}"))
    vm_free = _free_gb(VM_IMAGES_DIR)
    if vm_free < MIN_VM_DISK_GB_WARNING:
        out.append(
            _c(
                "disque_vm",
                WARNING,
                f"{vm_free:.1f} GB free for VM disks ({VM_IMAGES_DIR}): little space",
                action="add a storage pool on another disk",
            )
        )
    else:
        out.append(_c("disque_vm", OK, f"{vm_free:.1f} GB free for VM disks"))

    ifaces = []
    for name in sorted(os.listdir("/sys/class/net")) if Path("/sys/class/net").exists() else []:
        if name == "lo":
            continue
        if _read(f"/sys/class/net/{name}/operstate").strip() == "up":
            ifaces.append(name)
    if ifaces:
        out.append(_c("reseau", OK, "Interface(s) active(s) : " + ", ".join(ifaces[:6])))
    else:
        out.append(_c("reseau", WARNING, "No active network interface: updates and remote access are unavailable"))

    if _port_in_use(8000) and not _service_active("hyperlite"):
        out.append(
            _c(
                "port_8000",
                WARNING,
                "Port 8000 is already used by another process",
                feature="interface web Hyperlite",
                action="free port 8000",
            )
        )
    else:
        out.append(_c("port_8000", OK, "Port 8000 is available (or already served by hyperlite)"))

    sb = _secure_boot()
    zfs_feat = "pools de stockage ZFS"
    if _zfs_module_loaded():
        out.append(_c("zfs", OK, "ZFS module loaded"))
    elif sb == "active":
        out.append(
            _c(
                "zfs",
                DISABLED,
                "Secure Boot is active: an unsigned ZFS/DKMS module will be refused at load time",
                feature=zfs_feat,
                action="enroll the MOK key (mokutil --import) or disable Secure Boot",
            )
        )
    else:
        out.append(
            _c(
                "zfs",
                DISABLED,
                "ZFS module not loaded (optional)",
                feature=zfs_feat,
                action="apt install zfsutils-linux (composant contrib)",
            )
        )

    url = _apt_source_url()
    if url is None:
        out.append(_c("depot_apt", OK, "No hyperlite apt repository configured (offline or local installation)"))
    elif urllib.parse.urlparse(url).scheme not in ("http", "https"):
        out.append(_c("depot_apt", OK, f"Repository {url} (non-HTTP scheme, network check skipped)"))
    elif offline:
        out.append(_c("depot_apt", OK, f"Repository {url} (network check skipped: --offline)"))
    else:
        try:
            # The URL scheme is validated by require_http_url() or is a constant https URL.
            with urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(url.rstrip("/") + "/dists/stable/InRelease", method="HEAD"),  # noqa: S310
                timeout=5,
            ):
                out.append(_c("depot_apt", OK, f"apt repository reachable: {url}"))
        except Exception as e:
            out.append(
                _c(
                    "depot_apt",
                    WARNING,
                    f"apt repository {url} unreachable ({type(e).__name__})",
                    feature="Hyperlite updates",
                    action="check the network / Tailscale",
                )
            )
    return out


def _parse_requirements(path):
    pins = {}
    for line in _read(path).splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s#;]+)", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


_PROBE = r"""
import importlib, importlib.metadata as md, json, sys
mods, dists, ws = json.loads(sys.argv[1])
res = {"modules": {}, "versions": {}, "websocket": None}
for m in mods:
    try:
        importlib.import_module(m); res["modules"][m] = None
    except BaseException as e:
        res["modules"][m] = type(e).__name__ + ": " + str(e)[:150]
for m in ws:
    try:
        importlib.import_module(m); res["websocket"] = m; break
    except BaseException:
        pass
for d in dists:
    try: res["versions"][d] = md.version(d)
    except Exception: res["versions"][d] = None
print(json.dumps(res))
"""


def probe_python(python=None):
    """REAL import of each module in the target interpreter (not a simple
    find_spec: a module that is present but broken, e.g. libvirt without its
    .so files, must be detected). python=None: the current interpreter,
    in-process."""
    mods = [m[0] for m in PYTHON_MODULES]
    dists = [m[1] for m in PYTHON_MODULES] + ["websockets"]
    args = json.dumps([mods, dists, list(WEBSOCKET_MODULES)])
    if python is None:
        python = sys.executable
    r = subprocess.run([python, "-c", _PROBE, args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return json.loads(r.stdout)


def check_python(python=None, requirements=None):
    if python is not None and not os.access(python, os.X_OK):
        return [
            _c(
                "python_interpreteur",
                BLOCKING,
                f"Interpreter {python} not found",
                action="create the venv, then install requirements.txt",
            )
        ]
    try:
        probe = probe_python(python)
    except Exception as e:
        return [_c("python_interpreteur", BLOCKING, f"Python interpreter unusable: {e}")]
    out = []
    pins = _parse_requirements(requirements) if requirements else {}
    for mod, dist, gravite, feat in PYTHON_MODULES:
        err = probe["modules"].get(mod)
        if err is None:
            ver = probe["versions"].get(dist)
            want = pins.get(dist.lower().replace("_", "-"))
            if want and ver and ver != want:
                out.append(
                    _c(
                        f"python:{mod}",
                        WARNING,
                        f"{dist} {ver} installed, {want} expected (requirements.txt)",
                        action="pip install -r requirements.txt",
                    )
                )
            else:
                out.append(_c(f"python:{mod}", OK, f"{dist} {ver or ''} importable".replace("  ", " ")))
        else:
            out.append(
                _c(
                    f"python:{mod}",
                    gravite,
                    f"{dist} non importable : {err}",
                    feature=feat,
                    action="pip install -r requirements.txt",
                )
            )
    if probe["websocket"]:
        out.append(_c("python:websocket", OK, f"WebSocket library: {probe['websocket']}"))
    else:
        out.append(
            _c(
                "python:websocket",
                DISABLED,
                "No WebSocket library (websockets/wsproto): install uvicorn[standard]",
                feature=WEBSOCKET_FEATURE,
                action="pip install 'uvicorn[standard]'",
            )
        )
    return out


def summarize(checks):
    counts = {s: sum(1 for c in checks if c["statut"] == s) for s in (OK, WARNING, DISABLED, BLOCKING)}
    return {
        "bloquant": counts[BLOCKING] > 0,
        "compte": counts,
        "fonctionnalites_desactivees": sorted(
            {c["fonctionnalite"] for c in checks if c["statut"] in (DISABLED, BLOCKING) and c.get("fonctionnalite")}
        ),
    }


def run(python=None, requirements=None, only=None, offline=False):
    checks = []
    if only in (None, "system"):
        checks += check_system(offline=offline)
    if only in (None, "python"):
        checks += check_python(python, requirements)
    return {"resume": summarize(checks), "controles": checks}


_ICON = {OK: "[ OK ]", WARNING: "[WARN]", DISABLED: "[OFF ]", BLOCKING: "[FAIL]"}


def format_report(report):
    lines = ["Hyperlite -- preflight check", ""]
    for c in report["controles"]:
        lines.append(f"{_ICON[c['statut']]} {c['message']}")
        if c["statut"] != OK:
            if c.get("fonctionnalite"):
                lines.append(f"       impact : {c['fonctionnalite']}")
            if c.get("action"):
                lines.append(f"       action : {c['action']}")
    r = report["resume"]["compte"]
    lines += [
        "",
        f"Summary: {r[OK]} ok, {r[WARNING]} warning(s), {r[DISABLED]} disabled feature(s), {r[BLOCKING]} blocking",
    ]
    if report["resume"]["bloquant"]:
        lines.append("=> Installation NOT recommended: fix the [FAIL] items above.")
    elif r[DISABLED] or r[WARNING]:
        lines.append("=> Installation possible in degraded mode (see [OFF]/[WARN]).")
    else:
        lines.append("=> Everything is in order.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--python", help="interpreter to probe (e.g. the venv)")
    ap.add_argument("--requirements", help="requirements.txt, to compare versions")
    ap.add_argument("--only", choices=["system", "python"])
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    report = run(a.python, a.requirements, a.only, a.offline)
    print(json.dumps(report, ensure_ascii=False, indent=2) if a.json else format_report(report))
    return 1 if report["resume"]["bloquant"] else 0


if __name__ == "__main__":
    sys.exit(main())
