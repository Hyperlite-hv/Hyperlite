"""The root part of a migration between clusters (docs/design/multi-cluster.md, section 9), run by hyperlite-daemon
(app/daemon/ops/transfer.py): at the source, a stopped VM's specification and its disks read; at the target, the
volumes of an incoming VM, its disks written, its definition and its cancellation.

A disk is read or written through a `qemu-nbd` export under RUN, root's, and a helper of the system's Python
(site_transfer_nbd.py) that turns it into frames on a pipe; the daemon hands the API the pipe's other end, never a
path. The target's incomings are kept in STATE (root's, 0700), each a JSON file named by its id: the daemon finds
their volumes there, never in what it is asked. One that saw nothing for IDLE_S is removed with its volumes."""

import contextlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import paths, site_spec

logger = logging.getLogger(__name__)

STATE = paths.VAR / "transfer"
RUN = paths.RUN / "migration"
IDLE_S = 3600
HELPER = Path(__file__).with_name("site_transfer_nbd.py")
POOLS = ("dir", "netfs", "logical")  # file pools (SMB ones are netfs) and LVM; iSCSI and ZFS not in this version
_ID = re.compile(r"^[0-9a-f]{32}$")

_jobs = {}  # (incoming or vm, dev) -> {"nbd", "helper", "socket"}
_lock = threading.Lock()


class TransferError(Exception):
    """A refusal or a failure, with the daemon's kind (app/daemon/registry.KINDS)."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


# ---- At the source ----


def _domain(conn, vm):
    try:
        return conn.lookupByName(vm)
    except libvirt.libvirtError:
        raise TransferError("not_found", f"VM '{vm}' not found") from None


def _disks(root):
    """{target dev: (path, format)} of the VM's disks."""
    out = {}
    for disk in root.findall("devices/disk[@device='disk']"):
        source, target, driver = disk.find("source"), disk.find("target"), disk.find("driver")
        path = source.get("file") or source.get("dev") if source is not None else None
        if path and target is not None:
            out[target.get("dev")] = (path, (driver.get("type") if driver is not None else None) or "raw")
    return out


def describe(conn, vm):
    """What the source sends the target first: {"spec", "left", "allocated": {dev: bytes}}, for a stopped VM that has
    no snapshot and no device of this host."""
    domain = _domain(conn, vm)
    if domain.isActive():
        raise TransferError("refused", f"{vm} is running: shut it down first")
    if domain.snapshotNum(0):
        raise TransferError("refused", f"{vm} has {domain.snapshotNum(0)} snapshot(s): delete them first")
    xml = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    root = ET.fromstring(xml)
    problems = site_spec.refusals(root)
    if problems:
        raise TransferError("refused", f"{vm} cannot go to another cluster: " + "; ".join(problems))
    sizes, allocated = {}, {}
    for dev, (path, fmt) in _disks(root).items():
        info = _image_info(path)
        sizes[dev] = (info["virtual-size"], fmt)
        allocated[dev] = int(info.get("actual-size") or info["virtual-size"])
    spec, left = site_spec.from_domain(xml, sizes)
    return {"spec": spec, "left": left, "allocated": allocated}


def _image_info(path):
    r = subprocess.run(
        ["qemu-img", "info", "--output=json", "-U", "--", path], capture_output=True, text=True, timeout=60, check=False
    )
    try:
        if r.returncode != 0:
            raise ValueError(r.stderr.strip()[:300])
        return json.loads(r.stdout)
    except ValueError as e:
        raise TransferError("failed", f"{path} cannot be read: {e}") from None


def export(conn, vm, dev):
    """A pipe from which the stopped VM's disk `dev` comes as frames (site_transfer_nbd.py): (its read end, size)."""
    domain = _domain(conn, vm)
    if domain.isActive():
        raise TransferError("refused", f"{vm} is running: shut it down first")
    disks = _disks(ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)))
    if dev not in disks:
        raise TransferError("not_found", f"{vm} has no disk {dev}")
    path, fmt = disks[dev]
    size = _image_info(path)["virtual-size"]  # before the export holds the image
    key = (vm, dev)
    socket = _serve(key, path, fmt, readonly=True)
    helper = subprocess.Popen(
        ["python3", "-I", str(HELPER), "read", _uri(socket)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    # The pipe is the API's before the reaper starts: a communicate() that still saw it would watch an fd number this
    # close frees, which the next pipe of this process takes (a subprocess's own error pipe: it then never ends).
    fd = os.dup(helper.stdout.fileno())
    helper.stdout.close()
    helper.stdout = None
    with _lock:
        _jobs[key]["helper"] = helper
    threading.Thread(target=_reap, args=(key,), daemon=True, name=f"transfer-{vm}-{dev}").start()
    return fd, size


# ---- The exports and their helpers ----


def _uri(socket):
    return f"nbd+unix:///?socket={socket}"


def _serve(key, path, fmt, readonly):
    """A qemu-nbd export of `path` on a socket of RUN, for one client."""
    with _lock:
        if key in _jobs and "done" not in _jobs[key]:
            raise TransferError("in_use", f"{key[1]} of {key[0]} is already being moved")
        _jobs[key] = {}
    RUN.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN, 0o711)  # noqa: S103 - traversable, not listable: QEMU reaches a live migration's directory
    socket = RUN / f"{secrets.token_hex(8)}.sock"
    args = ["qemu-nbd", "--format", "raw" if fmt == "raw" else fmt, "--socket", str(socket), "--shared=1"]
    args += ["--read-only"] if readonly else ["--cache=none", "--discard=unmap"]
    process = subprocess.Popen([*args, "--", path], stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        if socket.exists() or process.poll() is not None:
            break
        time.sleep(0.05)
    if not socket.exists():
        process.kill()
        error = process.stderr.read().decode(errors="replace").strip()[:300] if process.stderr else ""
        with _lock:
            _jobs.pop(key, None)
        raise TransferError("failed", f"The disk could not be exported: {error}")
    with _lock:
        _jobs[key].update(nbd=process, socket=socket)
    return socket


def _reap(key):
    """Once a helper exits, its export stopped: (its exit code, its error output)."""
    with _lock:
        job = _jobs.get(key) or {}
    helper = job.get("helper")
    code, error = None, ""
    if helper is not None:
        _, err = helper.communicate()  # stdin and stdout are the API's: only the error output is read
        code, error = helper.returncode, (err or b"").decode(errors="replace").strip()[:300]
        if code:
            logger.warning("Transfer of %s: the helper failed: %s", key, error)
    _stop(job)
    with _lock:
        if _jobs.get(key) is job:
            _jobs[key] = {"done": code, "error": error}
    return code, error


def _stop(job):
    process = job.get("nbd")
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
    socket = job.get("socket")
    if socket is not None:
        Path(socket).unlink(missing_ok=True)


# ---- At the target ----


def _state_file(incoming):
    if not isinstance(incoming, str) or not _ID.match(incoming):
        raise TransferError("not_found", "No such incoming VM")
    return STATE / f"{incoming}.json"


def _load(incoming):
    try:
        return json.loads(_state_file(incoming).read_text())
    except FileNotFoundError:
        raise TransferError("not_found", "No such incoming VM (cancelled, or idle for an hour)") from None


def _save(state):
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    state["touched_at"] = time.time()
    path = _state_file(state["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def prepare(conn, spec, pools, allocated):
    """An incoming VM: its specification checked, its name and UUID free here, a new volume for each of its disks in
    the pool chosen for it. {"incoming", "disks": [{"dev", "size"}]}."""
    sweep(conn)
    try:
        spec = site_spec.check(spec)
    except site_spec.SpecError as e:
        raise TransferError("refused", str(e)) from None
    if len(pools) != len(spec["disks"]) or len(allocated) != len(spec["disks"]):
        raise TransferError("refused", "A pool and an allocated size for every disk")
    for lookup, value, what in (
        (conn.lookupByName, spec["name"], "name"),
        (conn.lookupByUUIDString, spec["uuid"], "UUID"),
    ):
        try:
            lookup(value)
        except libvirt.libvirtError:
            continue
        raise TransferError("in_use", f"A VM with this {what} already exists on this node")
    chosen = {}
    for name in set(pools):
        pool = _pool(conn, name)
        chosen[name] = pool
    need = {}
    for disk, name, used in zip(spec["disks"], pools, allocated, strict=True):
        thick = _pool_type(chosen[name]) == "logical"
        need[name] = need.get(name, 0) + (disk["size"] if thick else int(used))
    for name, bytes_needed in need.items():
        available = chosen[name].info()[3]
        if bytes_needed > available:
            raise TransferError(
                "refused", f"The pool {name} has {available // 1024**3} GiB free, {bytes_needed // 1024**3} GiB needed"
            )
    incoming = secrets.token_hex(16)
    state = {"id": incoming, "spec": spec, "created_at": time.time(), "disks": []}
    try:
        for n, (disk, name) in enumerate(zip(spec["disks"], pools, strict=True)):
            state["disks"].append(_create_volume(chosen[name], name, f"{spec['name']}-disk{n}", disk["size"]))
            _save(state)
    except TransferError:
        _remove_volumes(conn, state)
        raise
    return {"incoming": incoming, "disks": [{"dev": d["dev"], "size": d["size"]} for d in spec["disks"]]}


def _pool(conn, name):
    from app.core.libvirt_utils import lookup_pool

    try:
        pool = lookup_pool(conn, name)
    except libvirt.libvirtError:
        raise TransferError("not_found", f"Storage pool '{name}' not found") from None
    if _pool_type(pool) not in POOLS:
        raise TransferError("refused", f"The pool {name} cannot receive a VM from another cluster in this version")
    if not pool.isActive():
        raise TransferError("refused", f"The pool {name} is not started")
    return pool


def _pool_type(pool):
    return ET.fromstring(pool.XMLDesc(0)).get("type")


def _create_volume(pool, pool_name, base, size):
    thick = _pool_type(pool) == "logical"
    volume = ET.Element("volume")
    ET.SubElement(volume, "name").text = base if thick else f"{base}.qcow2"
    ET.SubElement(volume, "capacity", unit="bytes").text = str(size)
    if not thick:
        ET.SubElement(ET.SubElement(volume, "target"), "format", type="qcow2")
    try:
        vol = pool.createXML(ET.tostring(volume, encoding="unicode"), 0)
    except libvirt.libvirtError as e:
        raise TransferError("failed", f"The volume {base} could not be created in {pool_name}: {e}") from None
    return {"pool": pool_name, "volume": vol.name(), "path": vol.path(), "kind": "block" if thick else "file",
            "format": "raw" if thick else "qcow2", "size": size, "written": False}  # fmt: skip


def import_disk(incoming, dev):
    """A pipe into which the disk `dev` of the incoming VM goes as frames (site_transfer_nbd.py): (its write end, the
    disk's size)."""
    state = _load(incoming)
    n = next((i for i, d in enumerate(state["spec"]["disks"]) if d["dev"] == dev), None)
    if n is None:
        raise TransferError("not_found", f"The incoming VM has no disk {dev}")
    disk = state["disks"][n]
    key = (incoming, dev)
    socket = _serve(key, disk["path"], disk["format"], readonly=False)
    helper = subprocess.Popen(
        ["python3", "-I", str(HELPER), "write", _uri(socket), str(disk["size"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    fd = os.dup(helper.stdin.fileno())  # the API's before the reaper starts, as in export()
    helper.stdin.close()
    helper.stdin = None
    with _lock:
        _jobs[key]["helper"] = helper
    threading.Thread(
        target=_written, args=(incoming, n, key), daemon=True, name=f"transfer-{incoming[:8]}-{dev}"
    ).start()
    _save(state)
    return fd, disk["size"]


def _written(incoming, n, key):
    code, _ = _reap(key)
    if code == 0:
        try:
            state = _load(incoming)
        except TransferError:
            return
        state["disks"][n]["written"] = True
        _save(state)


def define(conn, incoming, networks, start=False, wait_s=120):
    """The incoming VM defined here once all its disks were written, from its checked specification, its new volumes
    and the networks chosen for its cards (each one of this node's); started if asked. {"name"}."""
    deadline = time.monotonic() + wait_s
    while True:
        state = _load(incoming)
        if all(d["written"] for d in state["disks"]):
            break
        with _lock:
            failed = [k[1] for k, j in _jobs.items() if k[0] == incoming and j.get("done") not in (None, 0)]
        if failed or time.monotonic() > deadline:
            raise TransferError("refused", f"Not every disk was written ({', '.join(failed) or 'still writing'})")
        time.sleep(0.5)
    for name in networks:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            raise TransferError("not_found", f"No network '{name}' on this node") from None
    try:
        xml = site_spec.to_domain(
            state["spec"], [(d["path"], d["kind"], d["format"]) for d in state["disks"]], networks
        )
    except site_spec.SpecError as e:
        raise TransferError("refused", str(e)) from None
    try:
        domain = conn.defineXML(xml)
    except libvirt.libvirtError as e:
        raise TransferError("failed", f"The VM could not be defined: {e}") from None
    _state_file(incoming).unlink(missing_ok=True)
    if start:
        try:
            domain.create()
        except libvirt.libvirtError as e:
            logger.warning("%s defined but not started: %s", state["spec"]["name"], e)
            return {"name": state["spec"]["name"], "started": False, "problem": str(e)}
    return {"name": state["spec"]["name"], "started": bool(start)}


def abort(conn, incoming):
    """The incoming VM cancelled: its exports stopped and its volumes removed."""
    state = _load(incoming)
    with _lock:
        keys = [k for k in _jobs if k[0] == incoming]
    for key in keys:
        with _lock:
            job = _jobs.pop(key, {})
        helper = job.get("helper")
        if helper is not None and helper.poll() is None:
            helper.kill()
        _stop(job)
    _remove_volumes(conn, state)
    _state_file(incoming).unlink(missing_ok=True)


def _remove_volumes(conn, state):
    for disk in state["disks"]:
        try:
            conn.storageVolLookupByPath(disk["path"]).delete(0)
        except libvirt.libvirtError as e:
            logger.warning("Volume %s of a cancelled incoming VM not removed: %s", disk["path"], e)


def sweep(conn):
    """Every incoming idle for IDLE_S removed, with its volumes."""
    if not STATE.is_dir():
        return
    for path in STATE.glob("*.json"):
        try:
            touched = json.loads(path.read_text()).get("touched_at", 0)
        except (OSError, ValueError):
            continue
        if time.time() - touched > IDLE_S:
            with _lock:
                busy = any(
                    k[0] == path.stem and j.get("helper") is not None and "done" not in j for k, j in _jobs.items()
                )
            if not busy:
                logger.info("Incoming VM %s idle for an hour: removed", path.stem)
                with contextlib.suppress(TransferError):  # cancelled meanwhile
                    abort(conn, path.stem)
