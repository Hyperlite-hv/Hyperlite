import logging
import re
import threading
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import (
    bridge_vlan,
    checkpoints,
    disk_move,
    disk_resize,
    passthrough,
    privileged,
    vm_devices,
    vm_locks,
)
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, pool_for_path
from app.core.listing import listing
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task, register_cancel
from app.core.vm_limits import validate_vm_resources
from app.daemon.registry import DaemonError
from app.routers.vms._shared import TARGET_DEV_RE, _get_ip, router

logger = logging.getLogger(__name__)


class DiskAttach(BaseModel):
    volume_name: str
    pool: str = "default"
    target_dev: str = "sdb"


@router.post("/{name}/disks", status_code=201)
def attach_disk(name: str, payload: DiskAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    perm.refuse_unless(user, "storage", payload.pool, "storage.allocate", f"storage pool '{payload.pool}'")
    if not TARGET_DEV_RE.match(payload.target_dev):
        log_action(user["username"], "attach_disk", name, "failed", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    # Found in its pool, checked (app/core/disk_images.py) and attached by hyperlite-daemon (app/core/vm_devices.py).
    privileged.ask(
        "vm.attach_disk",
        user,
        "attach_disk",
        name,
        vm=name,
        pool=payload.pool,
        volume=payload.volume_name,
        target=payload.target_dev,
    )
    log_action(user["username"], "attach_disk", name, "success")
    return {"message": f"Volume '{payload.volume_name}' attached to '{name}' as {payload.target_dev}"}


@router.delete("/{name}/disks/{target_dev}")
def detach_disk(name: str, target_dev: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "detach_disk", name, "failed", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_disk", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    privileged.ask("vm.detach_disk", user, "detach_disk", name, vm=name, target=target_dev)
    log_action(user["username"], "detach_disk", name, "success")
    return {"message": f"Disk '{target_dev}' detached from '{name}'"}


class DiskResize(BaseModel):
    # The new TOTAL size in GB, not an increment: repeating the request cannot grow the disk twice.
    size_gb: int = Field(ge=1)


@router.post("/{name}/disks/{target_dev}/resize")
def resize_disk(
    name: str, target_dev: str, payload: DiskResize, user: dict = Depends(require_vm_privilege("vm.resize"))
):
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "resize_disk", name, "failed", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
    if size_errors:
        log_action(user["username"], "resize_disk", name, "failed", "; ".join(size_errors))
        raise HTTPException(status_code=422, detail=size_errors)
    with vm_locks.claim_or_409(name, "a disk resize"):
        return _resize_disk(name, target_dev, payload, user)


def _resize_disk(name, target_dev, payload, user):
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "resize_disk", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        # Growing a disk takes space from its storage pool, as creating one does.
        if user["role"] != "admin":
            pool = disk_resize.storage_pool_name(conn, domain, target_dev)
            if pool is None:
                raise HTTPException(
                    status_code=403,
                    detail=f"The storage pool of '{target_dev}' is not known: an administrator grows it",
                )
            perm.refuse_unless(user, "storage", pool, "storage.allocate", f"storage pool '{pool}'")
        # Grown by hyperlite-daemon (app/daemon/ops/disk.py). qemu-img and libvirt refuse to resize a disk that
        # carries a replication bitmap: the daemon drops it once the request is accepted, so a refused one (a smaller
        # size, a busy disk) keeps the replication chain.
        try:
            grown = privileged.run(
                "disk.resize", user=user["username"], vm=name, target=target_dev, size_gb=payload.size_gb
            )
        except DaemonError as e:
            log_action(user["username"], "resize_disk", name, "failed", f"{target_dev}: {e.detail}")
            raise privileged.http_error(e) from None
        if grown["checkpoints_dropped"]:
            checkpoints.forget(name, grown["checkpoints_dropped"])
        old_bytes, new_bytes, live = grown["old_bytes"], grown["new_bytes"], grown["live"]
        old_gb, new_gb = round(old_bytes / disk_resize.GIB, 2), round(new_bytes / disk_resize.GIB, 2)
        log_action(
            user["username"],
            "resize_disk",
            name,
            "success",
            f"{target_dev}: {old_gb} GB -> {new_gb} GB ({'live' if live else 'stopped'})",
        )
        return {
            "target": target_dev,
            "old_size_gb": old_gb,
            "size_gb": new_gb,
            "live": live,
            "message": f"Disk '{target_dev}' of '{name}' grown to {new_gb} GB",
        }
    finally:
        conn.close()


class DiskMove(BaseModel):
    pool: str = Field(min_length=1, max_length=64)
    # Off by default: the original file stays in its pool until the admin deletes it, a cheap safety net.
    delete_source: bool = False


def _run_in_background(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


# A disk's copy is one call to hyperlite-daemon that waits: hours for the largest, as a backup's.
MOVE_TIMEOUT_S = 24 * 3600


def _move_disk_job(task_id, username, name, target_dev, dest_pool, delete_source):
    """The move, by hyperlite-daemon (app/daemon/ops/disk.py), which checks it all again, drops the replication
    checkpoints and writes the copy's progress into the task; cancelling the task stops the copy there."""
    register_cancel(task_id, lambda: privileged.run("disk.cancel", user=username, task_id=task_id))
    try:
        result = privileged.run(
            "disk.move",
            user=username,
            timeout=MOVE_TIMEOUT_S,
            vm=name,
            target=target_dev,
            pool=dest_pool,
            delete_source=delete_source,
            task_id=task_id,
        )
        if result["checkpoints_dropped"]:
            # A moved disk leaves its replication bitmap behind: the chain restarts with a full copy.
            checkpoints.forget(name, result["checkpoints_dropped"])
        finish_task(task_id, "succeeded")
        log_action(
            username,
            "move_disk",
            name,
            "success",
            f"{target_dev}: {result['source']} -> {result['destination']} "
            f"({'live' if result['live'] else 'stopped'}; source {'deleted' if result['source_deleted'] else 'kept'})",
        )
    except DaemonError as e:
        finish_task(task_id, "failed", e.detail)
        log_action(username, "move_disk", name, "failed", f"{target_dev}: {e.detail}")
    except Exception as e:
        logger.exception("Moving %s of %s failed", target_dev, name)
        finish_task(task_id, "failed", f"Internal error: {e}")
        log_action(username, "move_disk", name, "failed", f"{target_dev}: internal error: {e}")


@router.post("/{name}/disks/{target_dev}/move", status_code=202)
def move_disk(name: str, target_dev: str, payload: DiskMove, user: dict = Depends(require_role("admin"))):
    """Move a disk to another directory or NFS pool, live or stopped (see app/core/disk_move.py). Admin only, like
    a migration: it changes where the cluster's data lives."""
    if not TARGET_DEV_RE.match(target_dev):
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        try:
            how = disk_move.plan(conn, domain, target_dev, payload.pool)
        except disk_move.MoveError as e:
            log_action(user["username"], "move_disk", name, "failed", f"{target_dev}: {e.message}")
            raise HTTPException(status_code=e.status, detail=e.message) from e
    finally:
        conn.close()
    claim = vm_locks.claim_or_409(name, "a disk move")
    try:
        task_id = create_task("move_disk", name, node="local", username=user["username"])
        _run_in_background(
            vm_locks.released_after(claim, _move_disk_job),
            task_id,
            user["username"],
            name,
            target_dev,
            payload.pool,
            payload.delete_source,
        )
    except BaseException:
        claim.release()
        raise
    return {"task_id": task_id, "status": "running", "destination": how["dest"], "live": how["live"]}


def _get_interfaces(domain):
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    # VLANs on a VLAN-aware Linux bridge are in the VM's metadata, not in <vlan> (app/core/bridge_vlan.py).
    bridge_ports = bridge_vlan.ports_of_domain_xml(root)
    result = []
    for iface in root.findall(".//devices/interface"):
        mac_elem = iface.find("mac")
        port = bridge_ports.get((mac_elem.get("address") or "").lower()) if mac_elem is not None else None
        source_elem = iface.find("source")
        model_elem = iface.find("model")
        vlan_elem = iface.find("vlan/tag")
        filter_elem = iface.find("filterref")
        result.append(
            {
                "mac": mac_elem.get("address") if mac_elem is not None else None,
                "network": source_elem.get("network") if source_elem is not None else None,
                "type_source": iface.get("type"),
                "model": model_elem.get("type") if model_elem is not None else None,
                "vlan": int(vlan_elem.get("id"))
                if vlan_elem is not None and (vlan_elem.get("id") or "").isdigit()
                else (port["tag"] if port else None),
                "vlan_trunks": port["trunks"] if port else None,
                # The per-VM firewall is an nwfilter referenced by the interface.
                "firewall": filter_elem.get("filter") if filter_elem is not None else None,
            }
        )
    return result


def _disk_size(domain, conn, target, source):
    """Virtual size, space used on the host and pool of one disk. Each part is
    best-effort: an empty CD drive or a path outside any pool only lacks that part."""
    out = {"size_gb": None, "allocated_gb": None, "pool": None}
    if target:
        try:
            capacity, allocation, _physical = domain.blockInfo(target)
            out["size_gb"] = round(capacity / 1024**3, 2)
            out["allocated_gb"] = round(allocation / 1024**3, 2)
        except libvirt.libvirtError:
            logger.debug("No block info for %s", target, exc_info=True)
    if source:
        pool = pool_for_path(conn, source)
        if pool is not None:
            try:
                out["pool"] = pool.name()
            except libvirt.libvirtError:
                logger.debug("No pool name for %s", source, exc_info=True)
    return out


@router.get("/{name}/disks")
def get_vm_disks(name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_disks", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disks = []
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            source = disk.find("source")
            dev = target.get("dev") if target is not None else None
            path = (source.get("file") or source.get("dev")) if source is not None else None
            disks.append(
                {
                    "target": dev,
                    "bus": target.get("bus") if target is not None else None,
                    "type": disk.get("device"),
                    "source": path,
                    # null when the disk can be grown, else why not ("iscsi", "unsupported"); see disk_resize.
                    "not_growable": disk_resize.not_growable_code(disk),
                    **(
                        _disk_size(domain, conn, dev, path)
                        if path
                        else {"size_gb": None, "allocated_gb": None, "pool": None}
                    ),
                }
            )
        log_action(user["username"], "get_vm_disks", name, "success")
        return listing(disks)
    finally:
        conn.close()


@router.get("/{name}/network")
def get_vm_network(name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    conn = open_conn(node, readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_network", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        interfaces = _get_interfaces(domain)
        ip = _get_ip(domain) if domain.isActive() else None
        log_action(user["username"], "get_vm_network", name, "success")
        return {"interfaces": interfaces, "ip": ip}
    finally:
        conn.close()


class NetworkUpdate(BaseModel):
    network: str
    vlan_tag: int | None = Field(
        None,
        ge=1,
        le=4094,
        description="802.1Q tag, only on a network that carries tags (`vlan` in GET /networks): the interface's "
        "access VLAN, untagged in the VM",
    )
    vlan_trunks: str | None = Field(
        None,
        max_length=200,
        description="Trunk VLANs the VM sees tagged, such as `10,20-30`, on a VLAN-aware Linux bridge only "
        "(`vlan_mode` 'bridge' in GET /networks)",
    )


@router.put("/{name}/network")
def set_vm_network(
    name: str, payload: NetworkUpdate, mac: str | None = None, user: dict = Depends(require_vm_privilege("vm.hardware"))
):
    """mac: the interface to move, by its MAC address; absent, the first one. Moving it in place keeps its MAC address
    (what a DHCP reservation and the guest's own configuration know it by), where removing and adding it would not."""
    perm.refuse_unless(user, "network", payload.network, "network.assign", f"network '{payload.network}'")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    # Moved, with its VLANs, by hyperlite-daemon (app/core/vm_devices.py).
    privileged.ask(
        "vm.set_interface",
        user,
        "set_vm_network",
        name,
        vm=name,
        mac=mac,
        network=payload.network,
        vlan_tag=payload.vlan_tag,
        vlan_trunks=payload.vlan_trunks,
    )
    log_action(user["username"], "set_vm_network", name, "success")
    return {"message": f"VM '{name}' attached to network '{payload.network}'"}


MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class InterfaceAttach(BaseModel):
    network: str
    vlan_tag: int | None = Field(None, ge=1, le=4094)
    vlan_trunks: str | None = Field(None, max_length=200)


@router.post("/{name}/interfaces", status_code=201)
def attach_interface(name: str, payload: InterfaceAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    perm.refuse_unless(user, "network", payload.network, "network.assign", f"network '{payload.network}'")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    privileged.ask(
        "vm.attach_interface",
        user,
        "attach_interface",
        name,
        vm=name,
        network=payload.network,
        vlan_tag=payload.vlan_tag,
        vlan_trunks=payload.vlan_trunks,
    )
    log_action(user["username"], "attach_interface", name, "success")
    return {"message": f"Interface added on network '{payload.network}' for '{name}'"}


@router.delete("/{name}/interfaces/{mac}")
def detach_interface(name: str, mac: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not MAC_RE.match(mac):
        log_action(user["username"], "detach_interface", name, "failed", "Invalid MAC")
        raise HTTPException(status_code=422, detail="Invalid MAC address")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_interface", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    privileged.ask("vm.detach_interface", user, "detach_interface", name, vm=name, mac=mac)
    log_action(user["username"], "detach_interface", name, "success")
    return {"message": f"Interface '{mac}' detached from '{name}'"}


# --- Per-VM firewall ---
# Implemented through libvirt's nwfilter subsystem (VIR_NWFilter*) rather than
# hand-generated nftables/iptables rules: nwfilter is already libvirt's native
# mechanism for this, applied automatically by the QEMU driver at every VM
# (re)start with no external script to maintain. One filter per VM
# ("hyperlite-vm-<name>"), referenced by a <filterref> on each interface of the VM.


# --- Host devices given to a VM: PCI (VFIO) and USB passthrough (see app/core/passthrough.py). Administrators only:
# a PCI device is taken away from the host while the VM runs.


class HostDeviceAttach(BaseModel):
    device: str = Field(max_length=80)
    # A PCI device leaves the host's control while the VM runs: asked explicitly.
    confirm: bool = False


def _hostdev_listing(conn, domain):
    return [{k: v for k, v in e.items() if k != "xml"} for e in vm_devices.hostdev_entries(conn, domain)]


@router.get("/{name}/hostdevs")
def get_vm_hostdevs(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        return listing(_hostdev_listing(conn, domain))
    finally:
        conn.close()


@router.post("/{name}/hostdevs", status_code=201)
def attach_vm_hostdev(name: str, payload: HostDeviceAttach, user: dict = Depends(require_role("admin"))):
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_hostdev", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        # Checked against the host's own use of the device and given by hyperlite-daemon (app/core/vm_devices.py).
        added = privileged.ask(
            "vm.attach_hostdev", user, "attach_hostdev", name, vm=name, device=payload.device, confirm=payload.confirm
        )
        log_action(user["username"], "attach_hostdev", name, "success", ", ".join(added["addresses"]))
        return {"vm": name, "added": added["added"], "hostdevs": _hostdev_listing(conn, domain)}
    finally:
        conn.close()


@router.delete("/{name}/hostdevs/{device_id}")
def detach_vm_hostdev(name: str, device_id: str, user: dict = Depends(require_role("admin"))):
    if not passthrough.DEVICE_ID_RE.match(device_id):
        raise HTTPException(status_code=422, detail="Invalid device identifier")
    conn = open_conn(readonly=True)
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_hostdev", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    privileged.ask("vm.detach_hostdev", user, "detach_hostdev", name, vm=name, device=device_id)
    log_action(user["username"], "detach_hostdev", name, "success", device_id)
    return {"vm": name, "removed": device_id}
