import logging
import re
import threading
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import checkpoints, disk_move, disk_resize, passthrough, vm_locks
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import lookup_volume, open_conn, pool_for_path
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task, register_cancel, task_log, update_task_progress
from app.core.vm_limits import validate_vm_resources
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
    if not TARGET_DEV_RE.match(payload.target_dev):
        log_action(user["username"], "attach_disk", name, "echec", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    bus = DEV_BUS_PREFIXES.get(payload.target_dev[:2], "virtio")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if payload.target_dev.startswith("sd"):
            root = ET.fromstring(domain.XMLDesc(0))
            if any(t.get("bus") == "sata" for t in root.findall("./devices/disk[@device='disk']/target")):
                bus = "sata"
                if domain.isActive():
                    raise HTTPException(status_code=409, detail="Shut down the VM before adding a SATA disk")

        try:
            pool = conn.storagePoolLookupByName(payload.pool)
            vol = lookup_volume(pool, payload.volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "Volume not found")
            raise HTTPException(
                status_code=404, detail=f"Volume '{payload.volume_name}' not found in pool '{payload.pool}'"
            ) from None

        disk_xml = f"""
        <disk type='file' device='disk'>
          <driver name='qemu' type='qcow2'/>
          <source file='{vol.path()}'/>
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
            log_action(user["username"], "attach_disk", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Disk attach error: {msg}") from e

        log_action(user["username"], "attach_disk", name, "succes")
        return {"message": f"Volume '{payload.volume_name}' attached to '{name}' as {payload.target_dev}"}
    finally:
        conn.close()


@router.delete("/{name}/disks/{target_dev}")
def detach_disk(name: str, target_dev: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "detach_disk", name, "echec", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_disk", name, "echec", "VM not found")
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
            log_action(user["username"], "detach_disk", name, "echec", f"Disk {target_dev} not found")
            raise HTTPException(status_code=404, detail=f"Disk '{target_dev}' not found on VM '{name}'")

        disk_xml = ET.tostring(disk_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_disk", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e

        log_action(user["username"], "detach_disk", name, "succes")
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
        log_action(user["username"], "resize_disk", name, "echec", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
    if size_errors:
        log_action(user["username"], "resize_disk", name, "echec", "; ".join(size_errors))
        raise HTTPException(status_code=422, detail=size_errors)
    with vm_locks.claim_or_409(name, "a disk resize"):
        return _resize_disk(name, target_dev, payload, user)


def _resize_disk(name, target_dev, payload, user):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "resize_disk", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        try:
            # qemu-img and libvirt refuse to resize a disk that carries a replication bitmap.
            checkpoints.release(domain, name)
            old_bytes, new_bytes, live = disk_resize.grow(conn, domain, target_dev, payload.size_gb)
        except disk_resize.ResizeError as e:
            log_action(user["username"], "resize_disk", name, "echec", f"{target_dev}: {e.message}")
            raise HTTPException(status_code=e.status, detail=e.message) from e
        old_gb, new_gb = round(old_bytes / disk_resize.GIB, 2), round(new_bytes / disk_resize.GIB, 2)
        log_action(
            user["username"],
            "resize_disk",
            name,
            "succes",
            f"{target_dev}: {old_gb} GB -> {new_gb} GB ({'live' if live else 'stopped'})",
        )
        return {
            "cible": target_dev,
            "ancienne_taille_go": old_gb,
            "taille_go": new_gb,
            "a_chaud": live,
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


def _move_disk_job(task_id, username, name, target_dev, dest_pool, delete_source):
    conn = open_conn()
    try:
        domain = conn.lookupByName(name)
        # Checked again here: the VM may have changed (started, stopped, snapshotted) since the request.
        how = disk_move.plan(conn, domain, target_dev, dest_pool)
        # A moved disk leaves its replication bitmap behind: the chain restarts with a full copy.
        checkpoints.release(domain, name)
        stop = threading.Event()
        register_cancel(task_id, stop.set)
        task_log(task_id, f"Copying {target_dev} to {dest_pool} ({'live' if how['live'] else 'VM stopped'})")
        result = disk_move.move(
            domain, target_dev, how, delete_source, progress=lambda pct: update_task_progress(task_id, pct), stop=stop
        )
        finish_task(task_id, "termine")
        log_action(
            username,
            "move_disk",
            name,
            "succes",
            f"{target_dev}: {result['source']} -> {result['destination']} "
            f"({'live' if how['live'] else 'stopped'}; source {'deleted' if result['source_supprimee'] else 'kept'})",
        )
    except disk_move.MoveError as e:
        finish_task(task_id, "echec", e.message)
        log_action(username, "move_disk", name, "echec", f"{target_dev}: {e.message}")
    except Exception as e:
        logger.exception("Moving %s of %s failed", target_dev, name)
        finish_task(task_id, "echec", f"Internal error: {e}")
        log_action(username, "move_disk", name, "echec", f"{target_dev}: internal error: {e}")
    finally:
        conn.close()


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
            log_action(user["username"], "move_disk", name, "echec", f"{target_dev}: {e.message}")
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
    return {"task_id": task_id, "statut": "en_cours", "destination": how["dest"], "a_chaud": how["live"]}


def _get_interfaces(domain):
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    result = []
    for iface in root.findall(".//devices/interface"):
        mac_elem = iface.find("mac")
        source_elem = iface.find("source")
        model_elem = iface.find("model")
        vlan_elem = iface.find("vlan/tag")
        filter_elem = iface.find("filterref")
        result.append(
            {
                "mac": mac_elem.get("address") if mac_elem is not None else None,
                "reseau": source_elem.get("network") if source_elem is not None else None,
                "type_source": iface.get("type"),
                "modele": model_elem.get("type") if model_elem is not None else None,
                "vlan": int(vlan_elem.get("id"))
                if vlan_elem is not None and (vlan_elem.get("id") or "").isdigit()
                else None,
                # The per-VM firewall is an nwfilter referenced by the interface.
                "pare_feu": filter_elem.get("filter") if filter_elem is not None else None,
            }
        )
    return result


def _disk_size(domain, conn, target, source):
    """Virtual size, space used on the host and pool of one disk. Each part is
    best-effort: an empty CD drive or a path outside any pool only lacks that part."""
    out = {"taille_go": None, "alloue_go": None, "pool": None}
    if target:
        try:
            capacity, allocation, _physical = domain.blockInfo(target)
            out["taille_go"] = round(capacity / 1024**3, 2)
            out["alloue_go"] = round(allocation / 1024**3, 2)
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
            log_action(user["username"], "get_vm_disks", name, "echec", "VM not found")
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
                    "cible": dev,
                    "bus": target.get("bus") if target is not None else None,
                    "type": disk.get("device"),
                    "source": path,
                    # null when the disk can be grown, else why not ("iscsi", "non_gere"); see disk_resize.
                    "non_agrandissable": disk_resize.not_growable_code(disk),
                    **(
                        _disk_size(domain, conn, dev, path)
                        if path
                        else {"taille_go": None, "alloue_go": None, "pool": None}
                    ),
                }
            )
        log_action(user["username"], "get_vm_disks", name, "succes")
        return disks
    finally:
        conn.close()


@router.get("/{name}/network")
def get_vm_network(name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    conn = open_conn(node)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_network", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        interfaces = _get_interfaces(domain)
        ip = _get_ip(domain) if domain.isActive() else None
        log_action(user["username"], "get_vm_network", name, "succes")
        return {"interfaces": interfaces, "ip": ip}
    finally:
        conn.close()


class NetworkUpdate(BaseModel):
    network: str
    vlan_tag: int | None = Field(
        None,
        ge=1,
        le=4094,
        description="802.1Q tag: only effective if the underlying network/bridge handles trunking (Open vSwitch); silently ignored on a standard Linux bridge",
    )


@router.put("/{name}/network")
def set_vm_network(name: str, payload: NetworkUpdate, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{payload.network}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        iface = root.find(".//devices/interface")
        if iface is None:
            log_action(user["username"], "set_vm_network", name, "echec", "No interface")
            raise HTTPException(status_code=404, detail="No network interface found on this VM")

        source = iface.find("source")
        if source is None:
            source = ET.SubElement(iface, "source")
        for k in list(source.attrib):
            del source.attrib[k]
        source.set("network", payload.network)

        vlan_el = iface.find("vlan")
        if vlan_el is not None:
            iface.remove(vlan_el)
        if payload.vlan_tag is not None:
            vlan_el = ET.SubElement(iface, "vlan")
            ET.SubElement(vlan_el, "tag", {"id": str(payload.vlan_tag)})

        iface_xml = ET.tostring(iface, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.updateDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_network", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Network update error: {msg}") from e

        log_action(user["username"], "set_vm_network", name, "succes")
        return {"message": f"VM '{name}' attached to network '{payload.network}'"}
    finally:
        conn.close()


MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class InterfaceAttach(BaseModel):
    network: str
    vlan_tag: int | None = Field(None, ge=1, le=4094)


@router.post("/{name}/interfaces", status_code=201)
def attach_interface(name: str, payload: InterfaceAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "echec", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{payload.network}' not found") from None

        vlan_xml = f"<vlan><tag id='{payload.vlan_tag}'/></vlan>" if payload.vlan_tag is not None else ""
        root = ET.fromstring(domain.XMLDesc(0))
        model = root.find("./devices/interface/model")
        interface_model = "e1000e" if model is not None and model.get("type") == "e1000e" else "virtio"
        iface_xml = f"""
        <interface type='network'>
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
            log_action(user["username"], "attach_interface", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Interface attach error: {msg}") from e

        log_action(user["username"], "attach_interface", name, "succes")
        return {"message": f"Interface added on network '{payload.network}' for '{name}'"}
    finally:
        conn.close()


@router.delete("/{name}/interfaces/{mac}")
def detach_interface(name: str, mac: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not MAC_RE.match(mac):
        log_action(user["username"], "detach_interface", name, "echec", "Invalid MAC")
        raise HTTPException(status_code=422, detail="Invalid MAC address")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_interface", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        interfaces = root.findall(".//devices/interface")
        if len(interfaces) <= 1:
            log_action(user["username"], "detach_interface", name, "echec", "Last interface")
            raise HTTPException(status_code=422, detail="Cannot detach the last network interface of a VM")

        iface_elem = None
        for iface in interfaces:
            mac_elem = iface.find("mac")
            if mac_elem is not None and mac_elem.get("address", "").lower() == mac.lower():
                iface_elem = iface
                break
        if iface_elem is None:
            log_action(user["username"], "detach_interface", name, "echec", f"Interface {mac} not found")
            raise HTTPException(status_code=404, detail=f"Interface '{mac}' not found on VM '{name}'")

        iface_xml = ET.tostring(iface_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_interface", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e

        log_action(user["username"], "detach_interface", name, "succes")
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
        dev = next((by_key[k] for k in item["cles"] if k in by_key), None)
        out.append(
            {
                "id": dev["id"] if dev else item["cles"][0],
                "type": item["type"],
                "adresse": dev["adresse"] if dev else None,
                "fabricant": dev["fabricant"] if dev else None,
                "produit": dev["produit"] if dev else None,
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
        return _hostdev_listing(conn, domain)
    finally:
        conn.close()


@router.post("/{name}/hostdevs", status_code=201)
def attach_vm_hostdev(name: str, payload: HostDeviceAttach, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_hostdev", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        running = bool(domain.isActive())
        try:
            plan = passthrough.plan_attach(conn, name, payload.device, running)
        except passthrough.PassthroughError as e:
            log_action(user["username"], "attach_hostdev", name, "echec", e.message)
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
            log_action(user["username"], "attach_hostdev", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Device attach error: {msg}") from e
        log_action(user["username"], "attach_hostdev", name, "succes", ", ".join(d["adresse"] for d, _ in plan))
        return {"vm": name, "ajoutes": [d["id"] for d, _ in plan], "hostdevs": _hostdev_listing(conn, domain)}
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
            log_action(user["username"], "detach_hostdev", name, "echec", "VM not found")
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
            log_action(user["username"], "detach_hostdev", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Device detach error: {msg}") from e
        log_action(user["username"], "detach_hostdev", name, "succes", device_id)
        return {"vm": name, "retire": device_id}
    finally:
        conn.close()
