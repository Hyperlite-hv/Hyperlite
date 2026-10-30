"""The root part of a live migration between clusters (docs/design/multi-cluster.md, section 9.2), run by
hyperlite-daemon (app/daemon/ops/transfer.py, transfer.live_*): libvirt's own migration (as Proxmox VE's, the disks
mirrored while the VM runs, then its memory), carried by three Unix sockets per migration in RUN/<incoming>/, the same
path at both ends: `libvirt` (the target's libvirt, behind app/core/site_relay.py there), `memory` (QEMU's migration
stream) and `disks` (the target QEMU's NBD server). At the source they listen, the daemon hands them to the API, which
carries each connection through the pinned tunnel; at the target the daemon connects each channel the API opens.

QEMU binds and opens `memory` and `disks` itself: RUN/<incoming>/ is owned by its user, and AppArmor lets it
(scripts/ensure-backup-apparmor.sh's rule for /run/hyperlite/migration)."""

import grp
import logging
import os
import pwd
import shutil
import socket
import threading
import time
import xml.etree.ElementTree as ET

import libvirt

from app.core import site_relay, site_transfer
from app.core.site_transfer import RUN, TransferError

logger = logging.getLogger(__name__)

KINDS = ("libvirt", "memory", "disks")
LIBVIRT_SOCKET = "/var/run/libvirt/libvirt-sock"
QEMU_USER, QEMU_GROUP = "libvirt-qemu", "kvm"
FLAGS = (
    libvirt.VIR_MIGRATE_LIVE
    | libvirt.VIR_MIGRATE_PEER2PEER
    | libvirt.VIR_MIGRATE_NON_SHARED_DISK
    | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
    | libvirt.VIR_MIGRATE_AUTO_CONVERGE
)  # no PERSIST_DEST: the target defines the VM itself, from what it runs (live_define)

_running = {}  # incoming -> {"abort": callable}
_lock = threading.Lock()


def _dir(incoming):
    site_transfer._state_file(incoming)  # the id checked
    return RUN / incoming


def _qemu_ids():
    try:
        return pwd.getpwnam(QEMU_USER).pw_uid, grp.getgrnam(QEMU_GROUP).gr_gid
    except KeyError:
        return os.getuid(), os.getgid()  # no QEMU user here (the tests): this process's own


def _make_dir(incoming):
    path = _dir(incoming)
    RUN.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN, 0o711)  # noqa: S103 - traversable, not listable: QEMU reaches its own directory
    path.mkdir(mode=0o700, exist_ok=True)
    uid, gid = _qemu_ids()
    os.chown(path, uid, gid)
    os.chmod(path, 0o770)  # noqa: S103 - QEMU binds its sockets here: its user and group only
    return path


def close(incoming):
    """The migration's sockets removed, at either end."""
    shutil.rmtree(_dir(incoming), ignore_errors=True)


# ---- The target ----


def cpu_check(conn, cpu_xml):
    """Whether this host can run a VM with this <cpu> (libvirt's compareHypervisorCPU): {"compatible", "detail"}."""
    try:
        ET.fromstring(cpu_xml)
    except ET.ParseError:
        raise TransferError("refused", "Not a CPU definition") from None
    try:
        result = conn.compareHypervisorCPU(None, None, None, None, cpu_xml, 0)
    except libvirt.libvirtError as e:
        return {"compatible": False, "detail": str(e)}
    names = {
        libvirt.VIR_CPU_COMPARE_INCOMPATIBLE: "incompatible",
        libvirt.VIR_CPU_COMPARE_IDENTICAL: "identical",
        libvirt.VIR_CPU_COMPARE_SUPERSET: "superset",
    }
    return {"compatible": result in (libvirt.VIR_CPU_COMPARE_IDENTICAL, libvirt.VIR_CPU_COMPARE_SUPERSET),
            "detail": names.get(result, str(result))}  # fmt: skip


def prepare(conn, incoming, networks):
    """The incoming VM, already prepared as for a cold move, made ready to arrive running: the networks its cards go
    on (checked here), and its sockets' directory. What the relay will accept: expected()."""
    state = site_transfer._load(incoming)
    if len(networks) != len(state["spec"]["nics"]):
        raise TransferError("refused", "A network for every card")
    for name in networks:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            raise TransferError("not_found", f"No network '{name}' on this node") from None
    state["live"] = {"networks": list(networks)}
    site_transfer._save(state)
    _make_dir(incoming)
    return {"sockets": str(_dir(incoming)) + "/", "volumes": [d["path"] for d in state["disks"]]}


def expected(conn, incoming):
    """What site_relay.check_destination_xml accepts for this incoming VM."""
    state = site_transfer._load(incoming)
    if "live" not in state:
        raise TransferError("refused", "This incoming VM does not arrive running")
    caps = ET.fromstring(conn.getCapabilities())
    emulators = {e.text.strip() for e in caps.iter("emulator") if e.text}
    return {
        "name": state["spec"]["name"],
        "uuid": state["spec"]["uuid"],
        "volumes": sorted(d["path"] for d in state["disks"]),
        "networks": sorted(set(state["live"]["networks"])),
        "emulators": sorted(emulators),
        "sockets": str(_dir(incoming)) + "/",
    }


def connect(incoming, kind):
    """A socket connected to this host's end of the channel `kind`: its libvirt (the API puts the relay between), or
    the target QEMU's migration stream or NBD server."""
    if kind not in KINDS:
        raise TransferError("refused", f"Unknown channel {kind}")
    state = site_transfer._load(incoming)
    if "live" not in state:
        raise TransferError("refused", "This incoming VM does not arrive running")
    path = LIBVIRT_SOCKET if kind == "libvirt" else str(_dir(incoming) / f"{kind}.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except OSError as e:
        sock.close()
        raise TransferError("unreachable", f"The {kind} channel is not open here: {e}") from None
    state["touched_at"] = time.time()
    site_transfer._save(state)
    return sock


def define(conn, incoming):
    """Once the VM runs here: defined from what it runs (checked again as the relay checked it), its incoming record
    and sockets removed. {"name"}."""
    want = expected(conn, incoming)
    try:
        domain = conn.lookupByName(want["name"])
    except libvirt.libvirtError:
        raise TransferError("not_found", f"{want['name']} does not run here: the migration did not finish") from None
    if not domain.isActive():
        raise TransferError("refused", f"{want['name']} is not running here")
    xml = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_MIGRATABLE)
    try:
        site_relay.check_destination_xml(xml, want)
    except site_relay.Refused as e:
        raise TransferError("refused", f"What runs is not what was prepared: {e}") from None
    conn.defineXML(xml)
    site_transfer._state_file(incoming).unlink(missing_ok=True)
    close(incoming)
    return {"name": want["name"]}


# ---- The source ----


def listen(incoming):
    """The three listening sockets of the migration here, for the API to accept on: [fd] in KINDS' order."""
    path = _make_dir(incoming)
    uid, gid = _qemu_ids()
    fds = []
    for kind in KINDS:
        name = path / f"{kind}.sock"
        name.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(name))
        if kind == "libvirt":
            os.chmod(name, 0o600)  # libvirtd (root) connects
        else:
            os.chown(name, uid, gid)  # QEMU connects
            os.chmod(name, 0o600)
        sock.listen(8)
        fds.append(sock.detach())
    return fds


def destination_xml(conn, vm, volumes, networks):
    """The running VM's migratable definition, its disks on the target's volumes and its cards on the chosen networks,
    in order."""
    domain = conn.lookupByName(vm)
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_MIGRATABLE))
    disks = root.findall("devices/disk[@device='disk']")
    nics = root.findall("devices/interface")
    if len(disks) != len(volumes) or len(nics) != len(networks):
        raise TransferError("refused", "A volume for every disk and a network for every card")
    devs = []
    for disk, path in zip(disks, volumes, strict=True):
        source = disk.find("source")
        for attr in ("file", "dev"):
            source.attrib.pop(attr, None)
        disk.set("type", "file")
        source.set("file", path)
        driver = disk.find("driver")
        if driver is not None:
            driver.set("type", "qcow2")  # the target's file pools make qcow2 volumes (site_transfer._create_volume)
        devs.append(disk.find("target").get("dev"))
    for nic, network in zip(nics, networks, strict=True):
        source = nic.find("source")
        nic.set("type", "network")
        for attr in list(source.attrib):
            source.attrib.pop(attr)
        source.set("network", network)
    return ET.tostring(root, encoding="unicode"), devs


def run(conn, vm, incoming, volumes, networks, limit_mib=None, progress=None):
    """libvirt's live migration of the running VM through this migration's sockets (listen() made them, the API
    carries them). Returns once the VM runs at the target; a cancellation (abort()) leaves it running here."""
    domain = conn.lookupByName(vm)
    if not domain.isActive():
        raise TransferError("refused", f"{vm} is not running: move it offline")
    xml, devs = destination_xml(conn, vm, volumes, networks)
    path = _dir(incoming)
    params = {
        libvirt.VIR_MIGRATE_PARAM_DEST_XML: xml,
        libvirt.VIR_MIGRATE_PARAM_URI: f"unix://{path}/memory.sock",
        libvirt.VIR_MIGRATE_PARAM_DISKS_URI: f"unix://{path}/disks.sock",
        libvirt.VIR_MIGRATE_PARAM_MIGRATE_DISKS: devs,
    }
    if limit_mib:
        params[libvirt.VIR_MIGRATE_PARAM_BANDWIDTH] = int(limit_mib)

    def abort():
        try:
            domain.abortJob()
        except libvirt.libvirtError as e:
            logger.info("No migration job to abort for %s: %s", vm, e)

    with _lock:
        _running[incoming] = {"abort": abort}
    stop = threading.Event()
    if progress:
        threading.Thread(target=_follow, args=(stop, vm, progress), daemon=True).start()
    try:
        domain.migrateToURI3(f"qemu+unix:///system?socket={path}/libvirt.sock", params, FLAGS)
    except libvirt.libvirtError as e:
        raise TransferError("failed", f"The live migration stopped: {e}; {vm} keeps running here") from None
    finally:
        stop.set()
        with _lock:
            _running.pop(incoming, None)
        close(incoming)
    return {"name": vm}


def abort(incoming):
    with _lock:
        job = _running.get(incoming)
    if job is None:
        raise TransferError("not_found", "No live migration of this incoming VM runs here")
    job["abort"]()


def _follow(stop, vm, progress):
    """libvirt's job read every 2 s on a connection of its own, its progress handed to `progress(percent)`."""
    from app.core import libvirt_utils

    try:
        with libvirt_utils.local_conn() as conn:
            domain = conn.lookupByName(vm)
            while not stop.wait(2):
                try:
                    info = domain.jobInfo()
                    if info[3] > 0:
                        progress(min(99, int(info[4] * 100 / info[3])))
                except libvirt.libvirtError:
                    continue
    except Exception:
        logger.debug("Progress of the live migration of %s not followed", vm, exc_info=True)
