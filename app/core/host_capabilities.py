"""Host capability discovery. Builds a normalized profile of what a host
(local or remote, through the same SSH/qemu+ssh:// mechanism as the rest of the
cluster) can actually do. It is the foundation of the VM limits derived from the
host, the "Compatibility and capabilities" page and the cluster compatibility
diagnostic.

Guiding principle: detect rather than assume. Every sub-function is best-effort
and never raises: a missing command or file returns an explicit `None`/`False`
instead of failing the whole profile (controlled degradation, no global
failure)."""

import importlib
import json
import logging
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import which

import libvirt

logger = logging.getLogger(__name__)

# --- Local only (reads of /proc and /sys) --------------------------


def _cpu_capabilities_local(conn):
    caps_xml = conn.getCapabilities()
    root = ET.fromstring(caps_xml)
    host_cpu = root.find("host/cpu")
    arch = host_cpu.findtext("arch") if host_cpu is not None else platform.machine()
    model = host_cpu.findtext("model") if host_cpu is not None else None

    # Hardware virtualization availability: deduced from the presence of a
    # capability domain of type='kvm' in the libvirt capabilities. This is more
    # reliable than parsing /proc/cpuinfo by hand (libvirt has already verified it,
    # including the cases where /dev/kvm exists but is unusable for some reason).
    kvm_disponible = any(
        dom.get("type") == "kvm" for guest in root.findall("guest") for dom in guest.findall("arch/domain")
    )

    numa_cells = root.findall(".//topology/cells/cell")
    numa_detail = [{"id": cell.get("id"), "cpus": len(cell.findall(".//cpus/cpu"))} for cell in numa_cells]

    return {
        "architecture": arch,
        "modele": model,
        "coeurs_logiques": os.cpu_count(),
        "virtualisation_materielle": kvm_disponible,
        "numa_noeuds": len(numa_detail) or None,
        "numa_detail": numa_detail or None,
    }


def _memory_capabilities_local():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    info[key] = int(rest.strip().split()[0])  # kB
    except (OSError, ValueError, IndexError):
        logger.debug("Ignored exception in _memory_capabilities_local()", exc_info=True)
    return {
        "totale_mo": round(info["MemTotal"] / 1024) if "MemTotal" in info else None,
        "disponible_mo": round(info["MemAvailable"] / 1024) if "MemAvailable" in info else None,
    }


def _zfs_module_loaded_local():
    """The `zfs` binary can be installed WITHOUT the kernel module actually being
    loaded (e.g. Secure Boot refusing a self-signed DKMS module, seen on a real
    machine). Checking only the binary (`which zfs`) would have reported ZFS as
    "available" while every pool creation fails in practice, exactly the kind
    of silent limitation the portability mandate forbids. `lsmod` is the
    direct, reliable signal (no `zpool` call, which could itself fail or hang)."""
    try:
        proc = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=3)
        return proc.returncode == 0 and any(
            line.split()[0] == "zfs" for line in proc.stdout.splitlines() if line.split()
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _storage_capabilities_local():
    from app.core import zfs_storage

    zfs_installe = zfs_storage.is_available()
    zfs_module_charge = _zfs_module_loaded_local() if zfs_installe else False
    result = {
        "zfs_installe": zfs_installe,
        "zfs_module_charge": zfs_module_charge,
        # "available" = really usable right now, not just installed. This is the field
        # the rest of the code (derived limits, the Compatibility page...) must use to
        # decide whether ZFS can be offered to the user.
        "zfs_disponible": zfs_installe and zfs_module_charge,
        "blocs": None,
    }
    try:
        proc = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            result["blocs"] = json.loads(proc.stdout).get("blockdevices")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        logger.debug("Ignored exception in _storage_capabilities_local()", exc_info=True)

    default_dir = Path("/var/lib/libvirt/images")
    probe_path = default_dir if default_dir.exists() else Path("/")
    try:
        import shutil as _shutil

        usage = _shutil.disk_usage(probe_path)
        result["pool_defaut_total_go"] = round(usage.total / (1024**3), 1)
        result["pool_defaut_disponible_go"] = round(usage.free / (1024**3), 1)
    except OSError:
        result["pool_defaut_total_go"] = None
        result["pool_defaut_disponible_go"] = None
    return result


def _network_capabilities_local(conn):
    interfaces = []
    try:
        for name in sorted(os.listdir("/sys/class/net")):
            if name == "lo":
                continue
            path = Path("/sys/class/net") / name
            try:
                etat = (path / "operstate").read_text().strip()
            except OSError:
                etat = "inconnu"
            interfaces.append(
                {
                    "nom": name,
                    "pont": (path / "bridge").exists(),
                    "vlan": (path / "phy80211").exists() is False and Path(f"/proc/net/vlan/{name}").exists(),
                    "etat": etat,
                }
            )
    except OSError:
        logger.debug("Ignored exception in _network_capabilities_local()", exc_info=True)

    reseaux_libvirt = []
    try:
        for net in conn.listAllNetworks():
            reseaux_libvirt.append({"nom": net.name(), "actif": bool(net.isActive())})
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _network_capabilities_local()", exc_info=True)

    return {"interfaces": interfaces, "reseaux_libvirt": reseaux_libvirt}


def _secure_boot_state_local():
    """Direct read of the EFI variable rather than `mokutil` (not always installed,
    see the real blocking case seen with ZFS under Secure Boot): portable to
    any UEFI machine without an external dependency. The first 4 bytes are the
    UEFI attributes, the last byte is the value."""
    if not Path("/sys/firmware/efi").exists():
        return "bios_legacy"
    efivars = Path("/sys/firmware/efi/efivars")
    if not efivars.exists():
        return "uefi_inconnu"
    for entry in efivars.glob("SecureBoot-*"):
        try:
            data = entry.read_bytes()
            if len(data) >= 5:
                return "active" if data[-1] == 1 else "inactive"
        except OSError:
            logger.debug("Ignored exception in _secure_boot_state_local()", exc_info=True)
            continue
    return "uefi_inconnu"


def _software_capabilities_local():
    binaries = ["qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx"]
    result = {b: which(b) is not None for b in binaries}
    # REAL imports in the Hyperlite process itself (not a simple find_spec): without
    # WebSocket support, the host shell, VM consoles and terminals fail silently on
    # the browser side (a real bug seen when uvicorn was installed without its
    # [standard] extra).
    from app.core import preflight

    deps = {}
    for mod, _dist, _gravite, _feat in preflight.PYTHON_MODULES:
        try:
            importlib.import_module(mod)
            deps[mod] = True
        except Exception:
            deps[mod] = False
    ws = None
    for mod in preflight.WEBSOCKET_MODULES:
        try:
            importlib.import_module(mod)
            ws = mod
            break
        except Exception:
            logger.debug("Ignored exception in _software_capabilities_local()", exc_info=True)
            continue
    deps["websocket"] = ws is not None
    result["bibliotheque_websocket"] = ws is not None
    result["dependances_python"] = deps
    return result


def _lxc_available_local():
    try:
        lxc_conn = libvirt.open("lxc:///system")
    except libvirt.libvirtError:
        return False
    if lxc_conn is None:
        return False
    lxc_conn.close()
    return True


def _virt_capabilities_local(conn):
    return {
        "hyperviseur": conn.getType(),
        "version_libvirt": conn.getLibVersion(),
        "version_hyperviseur": conn.getVersion(),
        "conteneurs_lxc_disponibles": _lxc_available_local(),
    }


def get_local_capabilities():
    """Capability profile of THIS host (the one running this code)."""
    conn = libvirt.open("qemu:///system")
    if conn is None:
        raise RuntimeError("Unable to open the local libvirt connection")
    try:
        return {
            "cpu": _cpu_capabilities_local(conn),
            "memoire": _memory_capabilities_local(),
            "stockage": _storage_capabilities_local(),
            "reseau": _network_capabilities_local(conn),
            "virtualisation": _virt_capabilities_local(conn),
            "securite": {"secure_boot": _secure_boot_state_local()},
            "logiciel": _software_capabilities_local(),
        }
    finally:
        conn.close()


# --- Remote (SSH + libvirt through qemu+ssh://, the same mechanism as the rest
# of the cluster) ---------------------------------------

# A SINGLE shell script (one SSH round trip, like everywhere else in this
# project, e.g. app/core/ha.py::_attempt_ssh_fence) rather than several separate
# commands: every output line is prefixed with a stable label, parsed on the
# Python side. Best-effort: a command that is missing on the remote host yields an
# empty line for its label instead of failing the whole script (thanks to
# `|| true` / `2>/dev/null`).
_REMOTE_PROBE_SCRIPT = r"""
echo "MEMTOTAL:$(grep -m1 MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')"
echo "MEMAVAIL:$(grep -m1 MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}')"
echo "CPUCOUNT:$(nproc 2>/dev/null)"
echo "ARCH:$(uname -m 2>/dev/null)"
echo "SECUREBOOT:$([ -d /sys/firmware/efi ] && ([ -d /sys/firmware/efi/efivars ] && (for f in /sys/firmware/efi/efivars/SecureBoot-*; do [ -f "$f" ] && od -An -tu1 "$f" | tr -s ' ' | tail -c2; break; done) || echo unknown) || echo bios_legacy)"
for b in qemu-img virsh zfs zpool git gh xorriso nginx; do
  echo "BIN_${b}:$(command -v $b >/dev/null 2>&1 && echo 1 || echo 0)"
done
echo "ZFSMODULE:$(lsmod 2>/dev/null | awk '$1=="zfs"{print 1}')"
echo "DISKUSAGE:$(df -k /var/lib/libvirt/images 2>/dev/null | tail -1 | awk '{print $2, $4}')"
"""


def _parse_remote_probe(stdout):
    values = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        values[key] = val.strip()
    return values


def get_remote_capabilities(node_name):
    """Capability profile of a registered REMOTE node. It combines the libvirt API
    (which works natively through qemu+ssh://, transparent for
    CPU/NUMA/version/LXC) and a single SSH probe for the information that does
    not go through libvirt (memory, binaries, Secure Boot, disk usage). The
    same known limits as the rest of the cluster apply: the cluster SSH trust
    must already be established, see app/core/cluster.py."""
    from app.core.cluster import get_node, node_ssh_options
    from app.core.libvirt_utils import open_conn

    node = get_node(node_name)
    if not node:
        raise ValueError(f"Node '{node_name}' not found")

    conn = open_conn(node_name)
    try:
        libvirt_part = {
            "cpu": _cpu_capabilities_local(
                conn
            ),  # same function: reads ONLY through `conn`, already transparent for remote hosts
            "virtualisation": _virt_capabilities_local(conn),
            "reseau": {
                "reseaux_libvirt": [{"nom": n.name(), "actif": bool(n.isActive())} for n in conn.listAllNetworks()],
                "interfaces": None,
            },  # OS interfaces are not available without SSH, see below
        }
    finally:
        conn.close()

    ssh_part = {
        "memoire": {"totale_mo": None, "disponible_mo": None},
        "securite": {"secure_boot": None},
        "logiciel": {},
        "stockage": {"zfs_installe": None, "zfs_module_charge": None, "zfs_disponible": None, "blocs": None},
    }
    try:
        ssh_opts = node_ssh_options()
        target = f"{node['ssh_user']}@{node['hostname']}"
        proc = subprocess.run(
            ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), target, "bash", "-s"],
            input=_REMOTE_PROBE_SCRIPT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            v = _parse_remote_probe(proc.stdout)
            mem_total = v.get("MEMTOTAL")
            mem_avail = v.get("MEMAVAIL")
            ssh_part["memoire"] = {
                "totale_mo": round(int(mem_total) / 1024) if mem_total and mem_total.isdigit() else None,
                "disponible_mo": round(int(mem_avail) / 1024) if mem_avail and mem_avail.isdigit() else None,
            }
            sb = v.get("SECUREBOOT", "")
            ssh_part["securite"] = {
                "secure_boot": (
                    "active"
                    if sb == "1"
                    else "inactive"
                    if sb == "0"
                    else "bios_legacy"
                    if sb == "bios_legacy"
                    else "uefi_inconnu"
                )
            }
            ssh_part["logiciel"] = {
                b: v.get(f"BIN_{b}") == "1"
                for b in ("qemu-img", "virsh", "zfs", "zpool", "git", "gh", "xorriso", "nginx")
            }
            zfs_installe = bool(ssh_part["logiciel"].get("zfs") and ssh_part["logiciel"].get("zpool"))
            zfs_module_charge = v.get("ZFSMODULE") == "1"
            ssh_part["stockage"]["zfs_installe"] = zfs_installe
            ssh_part["stockage"]["zfs_module_charge"] = zfs_module_charge
            ssh_part["stockage"]["zfs_disponible"] = zfs_installe and zfs_module_charge
            disk = v.get("DISKUSAGE", "").split()
            if len(disk) == 2 and disk[0].isdigit() and disk[1].isdigit():
                ssh_part["stockage"]["pool_defaut_total_go"] = round(int(disk[0]) / (1024**2), 1)
                ssh_part["stockage"]["pool_defaut_disponible_go"] = round(int(disk[1]) / (1024**2), 1)
    except (OSError, subprocess.TimeoutExpired):
        logger.debug(
            "Ignored exception in get_remote_capabilities()", exc_info=True
        )  # best-effort: a partial profile (libvirt only) rather than a total failure

    return {
        "cpu": libvirt_part["cpu"],
        "memoire": ssh_part["memoire"],
        "stockage": ssh_part["stockage"],
        "reseau": libvirt_part["reseau"],
        "virtualisation": libvirt_part["virtualisation"],
        "securite": ssh_part["securite"],
        "logiciel": ssh_part["logiciel"],
    }


def get_capabilities(node_name=None):
    """Single entry point: the local profile if node_name is None/"local", the
    remote profile otherwise (the same convention as open_conn() and the rest
    of the project)."""
    if not node_name or node_name == "local":
        return get_local_capabilities()
    return get_remote_capabilities(node_name)
