"""A VM's own firewall: one libvirt nwfilter per VM ("hyperlite-vm-<name>"), referenced by a <filterref> on each of
its interfaces, applied by the QEMU driver at every (re)start with no external script (app/routers/vms/firewall.py).
hyperlite-daemon writes it on this node (app/daemon/ops/vm.py, docs/design/privilege-separation.md, section 13); the
filter's definition is built here with ElementTree from checked rules."""

import xml.etree.ElementTree as ET

import libvirt

from app.core.error_messages import describe_exception

PROTOCOLS = {"tcp", "udp", "icmp", "all"}
ACTIONS = {"accept", "drop"}
DIRECTIONS = {"in", "out", "inout"}


class FirewallError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def filter_name(vm_name):
    return f"hyperlite-vm-{vm_name}"


def filter_xml(vm_name, config):
    """The nwfilter of `config` ({"default_policy", "rules": [{"action", "direction", "protocol", "port"}]}): the rules
    in order, then the default policy as a catch-all at priority 999."""
    root = ET.Element("filter", name=filter_name(vm_name), chain="root")
    for priority, rule in enumerate(config["rules"], start=300):
        rule_el = ET.SubElement(
            root, "rule", action=rule["action"], direction=rule["direction"], priority=str(priority)
        )
        match = ET.SubElement(rule_el, rule["protocol"])
        if rule.get("port") and rule["protocol"] in ("tcp", "udp"):
            match.set("dstportstart", str(rule["port"]))
    default = "accept" if config["default_policy"] == "accept" else "drop"
    ET.SubElement(ET.SubElement(root, "rule", action=default, direction="inout", priority="999"), "all")
    return ET.tostring(root, encoding="unicode")


def parse(xml_desc):
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
            default_policy = action  # the catch-all rule added by filter_xml
            continue
        rules.append(
            {"action": action, "direction": direction, "protocol": protocol, "port": int(port) if port else None}
        )
    return {"default_policy": default_policy, "rules": rules}


def apply(conn, domain, config):
    """The filter defined, then referenced on EVERY interface of the VM (not only the first: a multi-NIC VM would
    otherwise leave one unfiltered). Returns the number of interfaces."""
    name = filter_name(domain.name())
    try:
        conn.nwfilterDefineXML(filter_xml(domain.name(), config))
    except libvirt.libvirtError as e:
        raise FirewallError("failed", f"Firewall definition error: {describe_exception(e)}") from e
    root = ET.fromstring(domain.XMLDesc(0))
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    applied = 0
    for iface in root.findall(".//devices/interface"):
        existing = iface.find("filterref")
        if existing is not None:
            iface.remove(existing)
        ET.SubElement(iface, "filterref", {"filter": name})
        try:
            domain.updateDeviceFlags(ET.tostring(iface, encoding="unicode"), flags)
            applied += 1
        except libvirt.libvirtError as e:
            raise FirewallError(
                "failed", f"Filter created but not applied to the interface: {describe_exception(e)}"
            ) from e
    return applied
