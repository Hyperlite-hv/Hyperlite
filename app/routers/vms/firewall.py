import logging
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)

_FIREWALL_PROTOCOLS = {"tcp", "udp", "icmp", "all"}
_FIREWALL_ACTIONS = {"accept", "drop"}
_FIREWALL_DIRECTIONS = {"in", "out", "inout"}


class FirewallRule(BaseModel):
    action: str
    direction: str
    protocol: str
    port: int | None = Field(None, ge=1, le=65535)


class FirewallConfig(BaseModel):
    default_policy: str = "accept"
    rules: list[FirewallRule] = []


def _firewall_filter_name(vm_name):
    return f"hyperlite-vm-{vm_name}"


def _build_nwfilter_xml(vm_name, config):
    rules_xml = ""
    priority = 300
    for rule in config.rules:
        port_attr = f" dstportstart='{rule.port}'" if rule.port and rule.protocol in ("tcp", "udp") else ""
        rules_xml += f"<rule action='{rule.action}' direction='{rule.direction}' priority='{priority}'><{rule.protocol}{port_attr}/></rule>"
        priority += 1
    default_action = "accept" if config.default_policy == "accept" else "drop"
    rules_xml += f"<rule action='{default_action}' direction='inout' priority='999'><all/></rule>"
    return f"<filter name='{_firewall_filter_name(vm_name)}' chain='root'>{rules_xml}</filter>"


def _parse_nwfilter_xml(xml_desc):
    root = ET.fromstring(xml_desc)
    rules = []
    default_policy = "accept"
    for rule_el in root.findall("rule"):
        proto_el = None
        for candidate in ("tcp", "udp", "icmp", "all"):
            proto_el = rule_el.find(candidate)
            if proto_el is not None:
                break
        if proto_el is None:
            continue
        protocol = proto_el.tag
        port = proto_el.get("dstportstart")
        direction = rule_el.get("direction", "inout")
        action = rule_el.get("action", "accept")
        if protocol == "all" and direction == "inout" and int(rule_el.get("priority", 0)) >= 999:
            default_policy = action  # the catch-all rule added by _build_nwfilter_xml
            continue
        rules.append(
            {"action": action, "direction": direction, "protocol": protocol, "port": int(port) if port else None}
        )
    return {"default_policy": default_policy, "rules": rules}


@router.get("/{name}/firewall")
def get_vm_firewall(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        try:
            nwf = conn.nwfilterLookupByName(_firewall_filter_name(name))
            return _parse_nwfilter_xml(nwf.XMLDesc(0))
        except libvirt.libvirtError:
            return {
                "default_policy": "accept",
                "rules": [],
            }  # no rule defined: everything is allowed, the default behaviour
    finally:
        conn.close()


@router.put("/{name}/firewall")
def set_vm_firewall(name: str, payload: FirewallConfig, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if payload.default_policy not in _FIREWALL_ACTIONS:
        raise HTTPException(status_code=422, detail="default_policy must be 'accept' or 'drop'")
    for rule in payload.rules:
        if (
            rule.action not in _FIREWALL_ACTIONS
            or rule.direction not in _FIREWALL_DIRECTIONS
            or rule.protocol not in _FIREWALL_PROTOCOLS
        ):
            raise HTTPException(status_code=422, detail=f"Invalid rule: {rule}")

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_firewall", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        filter_name = _firewall_filter_name(name)
        try:
            conn.nwfilterDefineXML(_build_nwfilter_xml(name, payload))
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_firewall", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Firewall definition error: {msg}") from e

        # Reference the filter on EVERY interface of the VM (not only the first one):
        # otherwise a multi-NIC VM would silently leave an interface unfiltered, which is
        # exactly the kind of bug fixed for disks/interfaces when cloning.
        root = ET.fromstring(domain.XMLDesc(0))
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        applied = 0
        for iface in root.findall(".//devices/interface"):
            existing_ref = iface.find("filterref")
            if existing_ref is not None:
                iface.remove(existing_ref)
            ET.SubElement(iface, "filterref", {"filter": filter_name})
            try:
                domain.updateDeviceFlags(ET.tostring(iface, encoding="unicode"), flags)
                applied += 1
            except libvirt.libvirtError as e:
                msg = describe_exception(e)
                log_action(user["username"], "set_vm_firewall", name, "echec", msg)
                raise HTTPException(
                    status_code=500, detail=f"Filter created but not applied to the interface: {msg}"
                ) from e

        log_action(
            user["username"], "set_vm_firewall", name, "succes", f"{len(payload.rules)} rule(s), {applied} interface(s)"
        )
        return {"message": f"Firewall applied to {applied} interface(s)", **payload.model_dump()}
    finally:
        conn.close()


# --- Snapshots ---
#
# A snapshot captures the state of a VM (disk, and memory if it is running) at an
# instant T, stored INSIDE the qcow2 file itself (an "internal" snapshot): it is
# fast to create and restore but it is NOT an independent backup (if the qcow2
# disk is lost or corrupted, so are all its snapshots). A real backup is a
# complete, self-contained copy of the data stored elsewhere, which survives the
# loss of the source disk: slower and heavier, but the only protection against a
# storage failure. A snapshot is for going back quickly (before a risky update,
# for example); a backup is for disaster recovery.
#
# Design notes:
# - The former code (flags=0, minimal XML, synchronous) created and restored
#   internal snapshots correctly. The real reproducible bug was that delete_vm()
#   called domain.undefine() WITHOUT a flag, which simply fails as soon as one or
#   more snapshots still exist ("cannot delete inactive domain with N
#   snapshots"). This is fixed above (VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA):
#   a VM on which a snapshot had been taken became impossible to delete from the
#   interface, which very probably explains the "snapshots do not work" feeling.
# - Deliberately rejected option: a "memoryless" snapshot on a RUNNING VM is
#   really an EXTERNAL snapshot on the libvirt side (a new overlay file, a chain of
#   backing files). It works at creation, but `revertToSnapshot()` returns "revert
#   to external snapshot not supported yet" with this QEMU/libvirt driver, so it
#   can NOT be restored. Offering an "include memory" checkbox that would produce
#   unrecoverable snapshots would have been a new trap, not a fix. The
#   memory/no-memory choice is therefore NOT exposed: memory is included
#   automatically if the VM is running (the only mode that restores reliably), and
#   the disk only if it is stopped (nothing else to capture).
# - Real duration: creating or restoring a snapshot with memory can take several
#   seconds (serializing the whole VM RAM into the qcow2). libvirt exposes NO
#   usable progress statistic for this operation (domain.jobStats() returns
#   {'type': VIR_DOMAIN_JOB_NONE} from start to end), so displaying a percentage
#   would be made up. Create and restore therefore run in the background (a
#   dedicated thread + a separate libvirt connection) while the HTTP endpoint
#   immediately returns a task_id (see app.core.tasks): the frontend shows an
#   indeterminate progress bar and the real elapsed time by following
#   GET /tasks/{id}, instead of blocking the request or showing a fake percentage.
