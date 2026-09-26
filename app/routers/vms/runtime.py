import logging
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_vm_privilege
from app.core.tasks import finish_task
from app.core.vm_builder import (
    get_automation_private_key_path,
    strip_install_boot_override,
)
from app.core.vm_meta import (
    clear_provisioning,
    get_provisioning,
    get_vm_ssh_user,
)
from app.routers.isos import ISOS_DIR
from app.routers.vms._shared import _get_ip, router

logger = logging.getLogger(__name__)


class CdromRequest(BaseModel):
    iso: str
    target_dev: Literal["hda", "hdb", "hdc", "hdd"] | None = None


@router.put("/{name}/cdrom")
def set_vm_cdrom(name: str, payload: CdromRequest, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_cdrom", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        iso_filename = Path(payload.iso).name
        if not iso_filename.lower().endswith(".iso"):
            raise HTTPException(status_code=422, detail="Invalid ISO name")
        iso_path = safe_child(ISOS_DIR, iso_filename)
        if not iso_path.exists():
            raise HTTPException(status_code=404, detail=f"ISO '{iso_filename}' not found")

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                target = disk.find("target")
                if payload.target_dev and target is not None and target.get("dev") == payload.target_dev:
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
                # IDE drives cannot be hot-plugged. Existing drives can still
                # change media live, including while Windows Setup is running.
                if domain.isActive():
                    raise HTTPException(status_code=409, detail="Shut down the VM before adding a CD drive")
                target_dev = payload.target_dev or "hdc"
                if any(target.get("dev") == target_dev for target in root.findall("./devices/disk/target")):
                    raise HTTPException(status_code=409, detail="This target is already used by a disk")
                new_cdrom_xml = (
                    '<disk type="file" device="cdrom">'
                    '<driver name="qemu" type="raw"/>'
                    f'<source file="{escape(str(iso_path))}"/>'
                    f'<target dev="{target_dev}" bus="ide"/>'
                    "<readonly/>"
                    "</disk>"
                )
                domain.attachDeviceFlags(new_cdrom_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "set_vm_cdrom", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Mount failed: {exc}") from exc

        log_action(user["username"], "set_vm_cdrom", name, "succes", iso_filename)
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
            log_action(user["username"], "eject_vm_cdrom", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                target = disk.find("target")
                if disk.get("device") == "cdrom" and (
                    target_dev is None or (target is not None and target.get("dev") == target_dev)
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
            log_action(user["username"], "eject_vm_cdrom", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Eject failed: {exc}") from exc

        log_action(user["username"], "eject_vm_cdrom", name, "succes")
        return {"vm": name, "ejecte": True}
    finally:
        conn.close()


def _stopped_metrics():
    return {
        "etat": "arrete",
        "cpu_pourcent": None,
        "memoire_allouee_mo": None,
        "memoire_utilisee_mo": None,
        "disques": [],
        "reseaux": [],
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
            log_action(user["username"], "get_vm_metrics", name, "echec", "VM not found")
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
        cpu_pourcent = round(max(0.0, min(100.0, ((cpu2 - cpu1) / (elapsed * 1e9)) * 100 / nvcpu)), 1)
        memoire_allouee_mo = round(info[2] / 1024, 1)

        memoire_utilisee_mo = None
        try:
            mem_stats = domain.memoryStats()
            if "available" in mem_stats and "unused" in mem_stats:
                memoire_utilisee_mo = round((mem_stats["available"] - mem_stats["unused"]) / 1024, 1)
            elif "rss" in mem_stats:
                memoire_utilisee_mo = round(mem_stats["rss"] / 1024, 1)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in get_vm_metrics()", exc_info=True)

        disques = []
        for dev in disk_devs:
            if dev in disk1 and dev in disk2:
                rd_rate = max(0, (disk2[dev][1] - disk1[dev][1]) / elapsed)
                wr_rate = max(0, (disk2[dev][3] - disk1[dev][3]) / elapsed)
                disques.append(
                    {
                        "cible": dev,
                        "lecture_ko_s": round(rd_rate / 1024, 1),
                        "ecriture_ko_s": round(wr_rate / 1024, 1),
                    }
                )

        reseaux = []
        for dev in iface_devs:
            if dev in net1 and dev in net2:
                rx_rate = max(0, (net2[dev][0] - net1[dev][0]) / elapsed)
                tx_rate = max(0, (net2[dev][4] - net1[dev][4]) / elapsed)
                reseaux.append(
                    {
                        "interface": dev,
                        "reception_ko_s": round(rx_rate / 1024, 1),
                        "emission_ko_s": round(tx_rate / 1024, 1),
                    }
                )

        log_action(user["username"], "get_vm_metrics", name, "succes")
        return {
            "etat": "actif",
            "cpu_pourcent": cpu_pourcent,
            "memoire_allouee_mo": memoire_allouee_mo,
            "memoire_utilisee_mo": memoire_utilisee_mo,
            "disques": disques,
            "reseaux": reseaux,
        }
    finally:
        conn.close()


# Beyond this delay without a working SSH, passive polling stops and the
# installation is declared failed. Before, a broken install (an incompatible ISO,
# a partitioning error, a network outage during the installation...) stayed "in
# progress" indefinitely and never reported an actionable error (seen in
# practice: several stale "ubuntu-autoinstall-fix" attempts in the audit log,
# never cleaned up). 30 minutes is generous for the supported families
# (kickstart/autoinstall), even on a slow disk.
PROVISIONING_TIMEOUT_S = 1800


@router.get("/{name}/provisioning")
def get_vm_provisioning(name: str, user: dict = Depends(get_current_user)):
    """State of an unattended installation (Kickstart/autoinstall) in progress, for the
    dashboard's progress bar. The signal used is whether a real SSH
    authentication with the Hyperlite automation key succeeds, NOT just "port 22
    answers" (seen in testing on Ubuntu: the live-server ISO runs its own sshd
    from the very start of the installation, long before the final system exists,
    so the port is reachable very early without our key being authorized there,
    which gave a false, premature "finished")."""
    prov = get_provisioning(name)
    if not prov:
        return {"provisioning": False}

    task_id = prov.get("task_id")

    def _fail(reason):
        clear_provisioning(name)
        if task_id:
            finish_task(task_id, "echec", reason)
        log_action(user["username"], "auto_install", name, "echec", reason)
        return {"provisioning": False, "failed": True, "erreur": reason}

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            return _fail("The VM disappeared during the unattended installation (deleted?)")

        started = datetime.fromisoformat(prov["started_at"])
        elapsed_s = int((datetime.now(UTC) - started).total_seconds())

        if elapsed_s > PROVISIONING_TIMEOUT_S:
            return _fail(f"Timeout: SSH still unreachable after {elapsed_s // 60} minutes")

        if not domain.isActive():
            return {"provisioning": True, "phase": "arretee", "os_family": prov["os_family"], "elapsed_s": elapsed_s}

        ip = _get_ip(domain)
        if not ip:
            return {"provisioning": True, "phase": "demarrage", "os_family": prov["os_family"], "elapsed_s": elapsed_s}

        username = get_vm_ssh_user(name)
        key_path = get_automation_private_key_path()
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=3",
                    "-i",
                    str(key_path),
                    f"{username}@{ip}",
                    "true",
                ],
                capture_output=True,
                timeout=6,
            )
        except subprocess.TimeoutExpired:
            return {
                "provisioning": True,
                "phase": "installation",
                "os_family": prov["os_family"],
                "elapsed_s": elapsed_s,
                "ip": ip,
            }

        if result.returncode == 0:
            clear_provisioning(name)
            # Ubuntu/autoinstall started on a kernel/initrd extracted from the ISO (see
            # create_vm, extract_casper_kernel) to add "autoinstall" to the command line. That
            # is no longer needed once the OS is installed on the disk, and leaving it would
            # make the VM reboot forever into the live installer instead of the installed
            # system (the normal <boot order>, on the disk, is never consulted while
            # <kernel>/<initrd> are present). The override is removed from the PERSISTENT XML
            # only: the VM keeps running without interruption with its current live
            # configuration until the next restart.
            if prov["os_family"] == "autoinstall":
                try:
                    current_xml = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
                    new_xml = strip_install_boot_override(current_xml)
                    if new_xml != current_xml:
                        conn.defineXML(new_xml)
                except (libvirt.libvirtError, ET.ParseError):
                    logger.debug("Ignored exception in get_vm_provisioning()", exc_info=True)
            if task_id:
                finish_task(task_id, "termine")
            log_action(user["username"], "provisioning_complete", name, "succes")
            return {"provisioning": False, "just_finished": True}
        return {
            "provisioning": True,
            "phase": "installation",
            "os_family": prov["os_family"],
            "elapsed_s": elapsed_s,
            "ip": ip,
        }
    finally:
        conn.close()


CONSOLE_TICKETS = {}
CONSOLE_TICKET_TTL = 30
