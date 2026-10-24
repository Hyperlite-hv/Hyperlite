import logging
import re
import threading
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import (
    bridge_vlan,
    checkpoints,
    disk_images,
    disk_move,
    disk_resize,
    network_edit,
    passthrough,
    privileged,
    vm_locks,
)
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import get_disk_paths_in_use, lookup_pool, lookup_volume, open_conn, pool_for_path
from app.core.listing import listing
from app.core.network_alloc import generate_mac
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


# Default buses for older guests; SATA guests inherit their existing disk bus.
DEV_BUS_PREFIXES = {"sd": "scsi", "vd": "virtio", "hd": "ide"}


@router.post("/{name}/disks", status_code=201)
def attach_disk(name: str, payload: DiskAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    perm.refuse_unless(user, "storage", payload.pool, "storage.allocate", f"storage pool '{payload.pool}'")
    if not TARGET_DEV_RE.match(payload.target_dev):
        log_action(user["username"], "attach_disk", name, "failed", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    bus = DEV_BUS_PREFIXES.get(payload.target_dev[:2], "virtio")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if payload.target_dev.startswith("sd"):
            root = ET.fromstring(domain.XMLDesc(0))
            if any(t.get("bus") == "sata" for t in root.findall("./devices/disk[@device='disk']/target")):
                bus = "sata"
                if domain.isActive():
                    raise HTTPException(status_code=409, detail="Shut down the VM before adding a SATA disk")

        try:
            pool = lookup_pool(conn, payload.pool)
            vol = lookup_volume(pool, payload.volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "failed", "Volume not found")
            raise HTTPException(
                status_code=404, detail=f"Volume '{payload.volume_name}' not found in pool '{payload.pool}'"
            ) from None

        path = vol.path()
        if path in get_disk_paths_in_use(conn):
            log_action(user["username"], "attach_disk", name, "failed", "Volume already used by a VM")
            raise HTTPException(status_code=409, detail=f"Volume '{payload.volume_name}' is already used by a VM")
        kind = vol.info()[0]
        if kind == libvirt.VIR_STORAGE_VOL_BLOCK:
            disk_type, source, fmt = "block", "dev", "raw"  # a logical volume, a LUN, a zvol
        elif kind == libvirt.VIR_STORAGE_VOL_FILE:
            # Declared with the format it really has, and only once it names no other file to read
            # (app/core/disk_images.py): QEMU reads what the definition says it is.
            try:
                fmt = disk_images.check(path)
            except disk_images.UnsafeImage as e:
                log_action(user["username"], "attach_disk", name, "failed", str(e))
                raise HTTPException(status_code=422, detail=str(e)) from None
            if fmt not in ("qcow2", "raw"):
                detail = f"Volume '{payload.volume_name}' is a {fmt} image: convert it to qcow2 to attach it"
                log_action(user["username"], "attach_disk", name, "failed", detail)
                raise HTTPException(status_code=422, detail=detail)
            disk_type, source = "file", "file"
        else:
            raise HTTPException(
                status_code=422, detail=f"Volume '{payload.volume_name}' is neither a file nor a device"
            )
        disk_xml = f"""
        <disk type='{disk_type}' device='disk'>
          <driver name='qemu' type='{fmt}'/>
          <source {source}={quoteattr(path)}/>
          <target dev='{payload.target_dev}' bus='{bus}'/>
        </disk>
        """
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.attachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "attach_disk", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Disk attach error: {msg}") from e

        log_action(user["username"], "attach_disk", name, "success")
        return {"message": f"Volume '{payload.volume_name}' attached to '{name}' as {payload.target_dev}"}
    finally:
        conn.close()


@router.delete("/{name}/disks/{target_dev}")
def detach_disk(name: str, target_dev: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "detach_disk", name, "failed", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_disk", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disk_elem = None
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            if target is not None and target.get("dev") == target_dev:
                disk_elem = disk
                break
        if disk_elem is None:
            log_action(user["username"], "detach_disk", name, "failed", f"Disk {target_dev} not found")
            raise HTTPException(status_code=404, detail=f"Disk '{target_dev}' not found on VM '{name}'")

        disk_xml = ET.tostring(disk_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_disk", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e

        log_action(user["username"], "detach_disk", name, "success")
        return {"message": f"Disk '{target_dev}' detached from '{name}'"}
    finally:
        conn.close()


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
    conn = open_conn()
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
    conn = open_conn()
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
    conn = open_conn(node)
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
    conn = open_conn(node)
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


def _lookup_network(conn, username, action, name, network, vlan_tag, vlan_trunks=None):
    """The network a VM interface is moved to or added on, and how it takes VLANs (network_edit.vlan_mode); 404 when
    missing, 422 when it cannot take the VLANs asked for."""
    try:
        net = conn.networkLookupByName(network)
    except libvirt.libvirtError:
        log_action(username, action, name, "failed", "Network not found")
        raise HTTPException(status_code=404, detail=f"Network '{network}' not found") from None
    # Without a VLAN asked for, how the network takes one does not matter (an interface's old VLANs are forgotten).
    mode = network_edit.vlan_mode(ET.fromstring(net.XMLDesc(0))) if vlan_tag is not None or vlan_trunks else None
    if (vlan_tag is not None or vlan_trunks) and mode is None:
        log_action(username, action, name, "failed", "VLAN tag unsupported by the network")
        raise HTTPException(status_code=422, detail=network_edit.VLAN_UNSUPPORTED.format(name=network))
    if vlan_trunks:
        if mode != "bridge":
            msg = f"Network '{network}': trunk VLANs are set on a VLAN-aware Linux bridge only"
            log_action(username, action, name, "failed", msg)
            raise HTTPException(status_code=422, detail=msg)
        try:
            bridge_vlan.parse_trunks(vlan_trunks)
        except bridge_vlan.VlanError as e:
            log_action(username, action, name, "failed", str(e))
            raise HTTPException(status_code=422, detail=str(e)) from None
    return net, mode


def _set_bridge_vlans(domain, mac, mode, tag, trunks):
    """Record the interface's VLANs in the VM's metadata when its network is a VLAN-aware Linux bridge (forgotten
    otherwise), and set them on its port at once on a running VM. Raises VlanError when the port could not be set:
    it is then left with no traffic."""
    if mode == "bridge":
        bridge_vlan.set_port(domain, mac, tag, trunks)
    elif mac.lower() in bridge_vlan.ports_of_domain_xml(ET.fromstring(domain.XMLDesc(0))):
        bridge_vlan.set_port(domain, mac, None, None)
    if mode == "bridge" and domain.isActive():
        # Setting the ports needs root: hyperlite-daemon reads the running VM's ports itself (app/daemon/ops/network.py).
        try:
            errors = privileged.run("network.vlan_apply_vm", vm=domain.name())["errors"]
        except DaemonError as e:
            raise bridge_vlan.VlanError(e.detail) from None
        if errors:
            raise bridge_vlan.VlanError("; ".join(errors))


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
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        _net, mode = _lookup_network(
            conn, user["username"], "set_vm_network", name, payload.network, payload.vlan_tag, payload.vlan_trunks
        )

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        interfaces = root.findall(".//devices/interface")
        if mac is not None:
            wanted = mac.strip().lower()
            interfaces = [
                i
                for i in interfaces
                if (i.find("mac") is not None and i.find("mac").get("address", "").lower() == wanted)
            ]
        iface = interfaces[0] if interfaces else None
        if iface is None:
            log_action(user["username"], "set_vm_network", name, "failed", "No interface")
            detail = (
                f"No network interface with the MAC address {mac} on this VM"
                if mac
                else "No network interface found on this VM"
            )
            raise HTTPException(status_code=404, detail=detail)

        source = iface.find("source")
        if source is None:
            source = ET.SubElement(iface, "source")
        for k in list(source.attrib):
            del source.attrib[k]
        source.set("network", payload.network)

        vlan_el = iface.find("vlan")
        if vlan_el is not None:
            iface.remove(vlan_el)
        if payload.vlan_tag is not None and mode == "native":
            vlan_el = ET.SubElement(iface, "vlan")
            ET.SubElement(vlan_el, "tag", {"id": str(payload.vlan_tag)})
        iface_mac = iface.find("mac").get("address") if iface.find("mac") is not None else None

        iface_xml = ET.tostring(iface, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.updateDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_network", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Network update error: {msg}") from e
        if iface_mac:
            try:
                _set_bridge_vlans(domain, iface_mac, mode, payload.vlan_tag, payload.vlan_trunks)
            except (bridge_vlan.VlanError, libvirt.libvirtError) as e:
                msg = describe_exception(e)
                log_action(user["username"], "set_vm_network", name, "failed", msg)
                raise HTTPException(status_code=500, detail=f"VLAN error: {msg}") from e

        log_action(user["username"], "set_vm_network", name, "success")
        return {"message": f"VM '{name}' attached to network '{payload.network}'"}
    finally:
        conn.close()


MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class InterfaceAttach(BaseModel):
    network: str
    vlan_tag: int | None = Field(None, ge=1, le=4094)
    vlan_trunks: str | None = Field(None, max_length=200)


@router.post("/{name}/interfaces", status_code=201)
def attach_interface(name: str, payload: InterfaceAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    perm.refuse_unless(user, "network", payload.network, "network.assign", f"network '{payload.network}'")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        _net, mode = _lookup_network(
            conn, user["username"], "attach_interface", name, payload.network, payload.vlan_tag, payload.vlan_trunks
        )

        vlan_xml = (
            f"<vlan><tag id='{payload.vlan_tag}'/></vlan>" if payload.vlan_tag is not None and mode == "native" else ""
        )
        # On a VLAN-aware bridge, chosen here rather than by libvirt: the interface's VLANs are recorded by its MAC.
        new_mac = generate_mac(conn) if mode == "bridge" else None
        root = ET.fromstring(domain.XMLDesc(0))
        model = root.find("./devices/interface/model")
        interface_model = "e1000e" if model is not None and model.get("type") == "e1000e" else "virtio"
        iface_xml = f"""
        <interface type='network'>
          {f"<mac address='{new_mac}'/>" if new_mac else ""}
          <source network='{payload.network}'/>
          {vlan_xml}
          <model type='{interface_model}'/>
        </interface>
        """
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.attachDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "attach_interface", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Interface attach error: {msg}") from e
        if mode == "bridge":
            try:
                _set_bridge_vlans(domain, new_mac, mode, payload.vlan_tag, payload.vlan_trunks)
            except (bridge_vlan.VlanError, libvirt.libvirtError) as e:
                msg = describe_exception(e)
                log_action(user["username"], "attach_interface", name, "failed", msg)
                raise HTTPException(status_code=500, detail=f"VLAN error: {msg}") from e

        log_action(user["username"], "attach_interface", name, "success")
        return {"message": f"Interface added on network '{payload.network}' for '{name}'"}
    finally:
        conn.close()


@router.delete("/{name}/interfaces/{mac}")
def detach_interface(name: str, mac: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not MAC_RE.match(mac):
        log_action(user["username"], "detach_interface", name, "failed", "Invalid MAC")
        raise HTTPException(status_code=422, detail="Invalid MAC address")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_interface", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        interfaces = root.findall(".//devices/interface")
        if len(interfaces) <= 1:
            log_action(user["username"], "detach_interface", name, "failed", "Last interface")
            raise HTTPException(status_code=422, detail="Cannot detach the last network interface of a VM")

        iface_elem = None
        for iface in interfaces:
            mac_elem = iface.find("mac")
            if mac_elem is not None and mac_elem.get("address", "").lower() == mac.lower():
                iface_elem = iface
                break
        if iface_elem is None:
            log_action(user["username"], "detach_interface", name, "failed", f"Interface {mac} not found")
            raise HTTPException(status_code=404, detail=f"Interface '{mac}' not found on VM '{name}'")

        iface_xml = ET.tostring(iface_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_interface", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e
        try:
            if mac.lower() in bridge_vlan.ports_of_domain_xml(root):
                bridge_vlan.set_port(domain, mac, None, None)
        except libvirt.libvirtError:
            logger.warning("VLANs of the detached interface %s of %s left in its metadata", mac, name, exc_info=True)

        log_action(user["username"], "detach_interface", name, "success")
        return {"message": f"Interface '{mac}' detached from '{name}'"}
    finally:
        conn.close()


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


def _hostdev_entries(conn, domain):
    """The VM's host devices, described from the host inventory, each with its <hostdev> XML (for a detach)."""
    inventory = passthrough.list_devices(conn)
    by_key = {}
    for dev in inventory["pci"] + inventory["usb"]:
        for key in passthrough.device_keys(dev):
            by_key[key] = dev
    out = []
    for item in passthrough.attached(domain):
        dev = next((by_key[k] for k in item["keys"] if k in by_key), None)
        out.append(
            {
                "id": dev["id"] if dev else item["keys"][0],
                "type": item["type"],
                "address": dev["address"] if dev else None,
                "vendor": dev["vendor"] if dev else None,
                "product": dev["product"] if dev else None,
                # A USB device can be unplugged; a PCI one can vanish after a hardware change.
                "present": dev is not None,
                "xml": item["xml"],
            }
        )
    return out


def _hostdev_listing(conn, domain):
    return [{k: v for k, v in e.items() if k != "xml"} for e in _hostdev_entries(conn, domain)]


@router.get("/{name}/hostdevs")
def get_vm_hostdevs(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
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
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_hostdev", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        running = bool(domain.isActive())
        try:
            plan = passthrough.plan_attach(conn, name, payload.device, running)
        except passthrough.PassthroughError as e:
            log_action(user["username"], "attach_hostdev", name, "failed", e.message)
            raise HTTPException(status_code=e.status, detail=e.message) from e
        if plan[0][0]["type"] == "pci" and not payload.confirm:
            raise HTTPException(
                status_code=422,
                detail="Giving a PCI device to a VM takes it away from the host while the VM runs: confirm with confirm",
            )
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG | (libvirt.VIR_DOMAIN_AFFECT_LIVE if running else 0)
        done = []
        try:
            for _dev, xml in plan:
                domain.attachDeviceFlags(xml, flags)
                done.append(xml)
        except libvirt.libvirtError as e:
            # A group is given whole or not at all.
            for xml in done:
                try:
                    domain.detachDeviceFlags(xml, flags)
                except libvirt.libvirtError:
                    logger.warning("Could not roll back %s on %s", xml, name, exc_info=True)
            msg = describe_exception(e)
            log_action(user["username"], "attach_hostdev", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Device attach error: {msg}") from e
        log_action(user["username"], "attach_hostdev", name, "success", ", ".join(d["address"] for d, _ in plan))
        return {"vm": name, "added": [d["id"] for d, _ in plan], "hostdevs": _hostdev_listing(conn, domain)}
    finally:
        conn.close()


@router.delete("/{name}/hostdevs/{device_id}")
def detach_vm_hostdev(name: str, device_id: str, user: dict = Depends(require_role("admin"))):
    if not passthrough.DEVICE_ID_RE.match(device_id):
        raise HTTPException(status_code=422, detail="Invalid device identifier")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_hostdev", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        item = next((e for e in _hostdev_entries(conn, domain) if e["id"] == device_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="This device is not given to this VM")
        running = bool(domain.isActive())
        if item["type"] == "pci" and running:
            raise HTTPException(status_code=409, detail="Shut down the VM before taking a PCI device back")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG | (libvirt.VIR_DOMAIN_AFFECT_LIVE if running else 0)
        try:
            domain.detachDeviceFlags(item["xml"], flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_hostdev", name, "failed", msg)
            raise HTTPException(status_code=500, detail=f"Device detach error: {msg}") from e
        log_action(user["username"], "detach_hostdev", name, "success", device_id)
        return {"vm": name, "removed": device_id}
    finally:
        conn.close()
