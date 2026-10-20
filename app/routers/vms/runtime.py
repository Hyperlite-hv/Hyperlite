import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import firmware
from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_vm_privilege
from app.routers.isos import ISOS_DIR
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


class CdromRequest(BaseModel):
    iso: str
    # A drive slot: the IDE name it has on a BIOS VM. A UEFI VM (q35, no IDE bus) has the same slots as SATA drives
    # (see firmware.cdrom_target()), so callers never need to know which one the VM is.
    target_dev: Literal["hda", "hdb", "hdc", "hdd"] | None = None


@router.put("/{name}/cdrom")
def set_vm_cdrom(name: str, payload: CdromRequest, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_cdrom", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        iso_filename = Path(payload.iso).name
        if not iso_filename.lower().endswith(".iso"):
            raise HTTPException(status_code=422, detail="Invalid ISO name")
        iso_path = safe_child(ISOS_DIR, iso_filename)
        if not iso_path.exists():
            raise HTTPException(status_code=404, detail=f"ISO '{iso_filename}' not found")

        root = ET.fromstring(domain.XMLDesc(0))
        vm_firmware = firmware.of_domain(root)
        slot_dev, slot_bus = firmware.cdrom_target(payload.target_dev or "hdc", vm_firmware)
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                target = disk.find("target")
                if payload.target_dev and target is not None and target.get("dev") == slot_dev:
                    if disk.get("device") != "cdrom":
                        raise HTTPException(status_code=409, detail="This target is already used by a disk")
                    cdrom = disk
                    break
                if disk.get("device") == "cdrom" and payload.target_dev is None:
                    cdrom = disk
                    break

        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

        try:
            if cdrom is not None:
                source = cdrom.find("source")
                if source is None:
                    source = ET.SubElement(cdrom, "source")
                source.set("file", str(iso_path))
                new_xml = ET.tostring(cdrom, encoding="unicode")
                domain.updateDeviceFlags(new_xml, flags)
            else:
                # IDE and SATA drives cannot be hot-plugged. Existing drives can still
                # change media live, including while Windows Setup is running.
                if domain.isActive():
                    raise HTTPException(status_code=409, detail="Shut down the VM before adding a CD drive")
                if any(target.get("dev") == slot_dev for target in root.findall("./devices/disk/target")):
                    raise HTTPException(status_code=409, detail="This target is already used by a disk")
                new_cdrom_xml = (
                    '<disk type="file" device="cdrom">'
                    '<driver name="qemu" type="raw"/>'
                    f'<source file="{escape(str(iso_path))}"/>'
                    f'<target dev="{slot_dev}" bus="{slot_bus}"/>'
                    "<readonly/>"
                    "</disk>"
                )
                domain.attachDeviceFlags(new_cdrom_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "set_vm_cdrom", name, "failed", str(exc))
            raise HTTPException(status_code=500, detail=f"Mount failed: {exc}") from exc

        log_action(user["username"], "set_vm_cdrom", name, "success", iso_filename)
        return {"vm": name, "iso": iso_filename}
    finally:
        conn.close()


@router.delete("/{name}/cdrom")
def eject_vm_cdrom(
    name: str,
    target_dev: Literal["hda", "hdb", "hdc", "hdd"] | None = None,
    user: dict = Depends(require_vm_privilege("vm.hardware")),
):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "eject_vm_cdrom", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        slot_dev = firmware.cdrom_target(target_dev, firmware.of_domain(root))[0] if target_dev else None
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                target = disk.find("target")
                if disk.get("device") == "cdrom" and (
                    slot_dev is None or (target is not None and target.get("dev") == slot_dev)
                ):
                    cdrom = disk
                    break
        if cdrom is None:
            raise HTTPException(status_code=404, detail="No CD drive on this VM")

        source = cdrom.find("source")
        if source is not None:
            cdrom.remove(source)

        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

        try:
            new_xml = ET.tostring(cdrom, encoding="unicode")
            domain.updateDeviceFlags(new_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "eject_vm_cdrom", name, "failed", str(exc))
            raise HTTPException(status_code=500, detail=f"Eject failed: {exc}") from exc

        log_action(user["username"], "eject_vm_cdrom", name, "success")
        return {"vm": name, "ejected": True}
    finally:
        conn.close()


def _stopped_metrics():
    return {
        "state": "stopped",
        "cpu_percent": None,
        "allocated_memory_mb": None,
        "used_memory_mb": None,
        "disks": [],
        "networks": [],
    }


def _is_active(domain):
    try:
        return bool(domain.isActive())
    except libvirt.libvirtError:
        return False  # the domain no longer exists


@router.get("/{name}/metrics")
def get_vm_metrics(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_metrics", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not domain.isActive():
            return _stopped_metrics()

        root = ET.fromstring(domain.XMLDesc(0))
        disk_devs = []
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target")
            if target is not None and target.get("dev"):
                disk_devs.append(target.get("dev"))
        iface_devs = []
        for iface in root.findall(".//devices/interface"):
            target = iface.find("target")
            if target is not None and target.get("dev"):
                iface_devs.append(target.get("dev"))

        def sample():
            cpu_time = domain.getCPUStats(True)[0]["cpu_time"]
            disk_samples = {}
            for dev in disk_devs:
                try:
                    disk_samples[dev] = domain.blockStats(dev)
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in sample()", exc_info=True)
            net_samples = {}
            for dev in iface_devs:
                try:
                    net_samples[dev] = domain.interfaceStats(dev)
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in sample()", exc_info=True)
            return cpu_time, disk_samples, net_samples

        try:
            cpu1, disk1, net1 = sample()
            t1 = time.time()
            time.sleep(0.4)
            cpu2, disk2, net2 = sample()
            t2 = time.time()
            info = domain.info()
        except libvirt.libvirtError:
            # The VM stopped (or was deleted) during the 0.4 s sampling window: report it as stopped
            # rather than a 500, the dashboard polls this while the user stops the VM.
            if not _is_active(domain):
                return _stopped_metrics()
            raise
        elapsed = max(t2 - t1, 0.001)

        nvcpu = info[3] or 1
        cpu_percent = round(max(0.0, min(100.0, ((cpu2 - cpu1) / (elapsed * 1e9)) * 100 / nvcpu)), 1)
        allocated_memory_mb = round(info[2] / 1024, 1)

        used_memory_mb = None
        try:
            mem_stats = domain.memoryStats()
            if "available" in mem_stats and "unused" in mem_stats:
                used_memory_mb = round((mem_stats["available"] - mem_stats["unused"]) / 1024, 1)
            elif "rss" in mem_stats:
                used_memory_mb = round(mem_stats["rss"] / 1024, 1)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in get_vm_metrics()", exc_info=True)

        disks = []
        for dev in disk_devs:
            if dev in disk1 and dev in disk2:
                rd_rate = max(0, (disk2[dev][1] - disk1[dev][1]) / elapsed)
                wr_rate = max(0, (disk2[dev][3] - disk1[dev][3]) / elapsed)
                disks.append(
                    {
                        "target": dev,
                        "read_kb_s": round(rd_rate / 1024, 1),
                        "write_kb_s": round(wr_rate / 1024, 1),
                    }
                )

        networks = []
        for dev in iface_devs:
            if dev in net1 and dev in net2:
                rx_rate = max(0, (net2[dev][0] - net1[dev][0]) / elapsed)
                tx_rate = max(0, (net2[dev][4] - net1[dev][4]) / elapsed)
                networks.append(
                    {
                        "interface": dev,
                        "received_kb_s": round(rx_rate / 1024, 1),
                        "sent_kb_s": round(tx_rate / 1024, 1),
                    }
                )

        log_action(user["username"], "get_vm_metrics", name, "success")
        return {
            "state": "active",
            "cpu_percent": cpu_percent,
            "allocated_memory_mb": allocated_memory_mb,
            "used_memory_mb": used_memory_mb,
            "disks": disks,
            "networks": networks,
        }
    finally:
        conn.close()


CONSOLE_TICKETS = {}
CONSOLE_TICKET_TTL = 30
