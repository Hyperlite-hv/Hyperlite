import ipaddress
import json
import logging
import subprocess
import xml.etree.ElementTree as ET

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import bridge_vlan, cluster_view, network_create, network_edit, network_firewall, privileged, renaming
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, open_lxc_conn_if_any
from app.core.listing import listing
from app.core.network_firewall import apply_network_firewall, get_network_firewall, remove_network_firewall
from app.core.security import get_current_user, require_role
from app.core.vm_builder import validate_name
from app.daemon.registry import DaemonError
from app.routers.vms._shared import inventory
from app.routers.vms.firewall import _FIREWALL_ACTIONS, _FIREWALL_DIRECTIONS, _FIREWALL_PROTOCOLS, FirewallConfig

logger = logging.getLogger(__name__)

# ?node= on every route: the member that holds the object does the work (app/core/cluster_view.py, on_node).
router = APIRouter(prefix="/networks", tags=["networks"], dependencies=[Depends(cluster_view.on_node)])


def _host_subnets():
    """(interface, IPv4 network) of the addresses configured on this host."""
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, timeout=10, check=True)
        links = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    found = []
    for link in links:
        for a in link.get("addr_info", []):
            try:
                found.append(
                    (link.get("ifname", "?"), ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
                )
            except (KeyError, ValueError):
                continue
    return found


def _subnet_conflict(conn, subnet):
    """A sentence naming what already uses an address range overlapping `subnet`, or None. libvirt refuses such a
    network only when it starts, with a message of its own, after defining it."""
    bridges = {}
    for net in conn.listAllNetworks():
        try:
            root = ET.fromstring(net.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError):
            continue
        bridge = root.find("bridge")
        if bridge is not None and bridge.get("name"):
            bridges[bridge.get("name")] = net.name()
        for ip in root.findall("ip"):
            try:
                mask = ip.get("netmask") or ip.get("prefix") or "24"
                other = ipaddress.ip_network(f"{ip.get('address')}/{mask}", strict=False)
            except (TypeError, ValueError):
                continue
            if other.version == 4 and other.overlaps(subnet):
                return f"{subnet} overlaps the network '{net.name()}' ({other})"
    for iface, other in _host_subnets():
        if other.overlaps(subnet) and iface not in bridges:
            return f"{subnet} overlaps the address of the host interface {iface} ({other})"
    return None


FORWARD_MODE_LABELS = {
    "nat": "nat",
    "route": "route",
    "bridge": "bridge",
    "open": "open",
    "private": "private",
    "vepa": "vepa",
    "passthrough": "passthrough",
    "hostdev": "hostdev",
}


def _network_summary(net):
    xml_desc = net.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    forward = root.find("forward")
    mode = forward.get("mode") if forward is not None else None
    bridge = root.find("bridge")
    bridge_name = bridge.get("name") if bridge is not None else None
    # A macvtap network has no bridge: it sits on a host interface named under <forward>.
    macvtap = root.find("forward/interface")
    if bridge_name is None and macvtap is not None:
        bridge_name = macvtap.get("dev")
    ip_elem = root.find("ip")
    dhcp = ip_elem is not None and ip_elem.find("dhcp") is not None
    vlan_mode = network_edit.vlan_mode(root)
    linux_bridge = bridge_vlan.linux_bridge_of_network(root)
    subnet = None
    if ip_elem is not None:
        subnet = {"address": ip_elem.get("address"), "netmask": ip_elem.get("netmask")}
    return {
        "name": net.name(),
        "uuid": net.UUIDString(),
        "active": net.isActive() == 1,
        "autostart": bool(net.autostart()),
        "bridge": bridge_name,
        "macvtap": macvtap is not None,
        "type": FORWARD_MODE_LABELS.get(mode, "isolated" if mode is None else mode),
        "network": subnet,
        "dhcp": dhcp,
        # "native": libvirt's <vlan> (Open vSwitch, SR-IOV); "bridge": a VLAN-aware Linux bridge, with trunks too.
        "vlan": vlan_mode is not None,
        "vlan_mode": vlan_mode,
        # A bridge network on a Linux bridge: whether it filters VLANs (it can be made to, POST .../vlan-aware), and
        # whether Hyperlite made it so (DELETE .../vlan-aware undoes that). None on any other network.
        "vlan_aware": vlan_mode == "bridge" if linux_bridge else None,
        "vlan_aware_by_hyperlite": linux_bridge in bridge_vlan.aware_bridges() if linux_bridge else None,
    }


def _ensure_isolated(conn, user):
    """The isolated network Hyperlite relies on, made by hyperlite-daemon only when it is missing or stopped: read here
    first, so a list of networks costs no call to the daemon."""
    try:
        net = conn.networkLookupByName("hyperlite-isolated")
        if net.isActive() and net.autostart():
            return
    except libvirt.libvirtError:
        pass
    try:
        privileged.run("network.ensure_isolated", user=user["username"])
    except DaemonError as e:
        logger.error("The isolated network could not be made: %s", e.detail)


@router.get("")
def list_networks(user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        _ensure_isolated(conn, user)
        result = []
        vm_counts = inventory(conn)["per_network"]
        for net in conn.listAllNetworks():
            try:
                summary = _network_summary(net)
            except libvirt.libvirtError:
                continue  # deleted between the listing and the read: it is simply no longer there
            summary["vms"] = vm_counts.get(summary["name"], 0)
            result.append(summary)
        log_action(user["username"], "list_networks", "networks", "success")
        return listing(result)
    finally:
        conn.close()


@router.get("/host-interfaces")
def host_interfaces(user: dict = Depends(require_role("admin"))):
    """The host's interfaces a bridged network can sit on (app/core/host_interfaces.py), read-only."""
    from app.core.host_interfaces import list_host_interfaces

    return listing(list_host_interfaces())


@router.get("/{name}")
def get_network(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_network", name, "failed", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{name}' not found") from None

        summary = _network_summary(net)
        leases = []
        if net.isActive():
            try:
                for lease in net.DHCPLeases():
                    leases.append(
                        {
                            "mac": lease.get("mac"),
                            "ip": lease.get("ipaddr"),
                            "hostname": lease.get("hostname"),
                            "expire": lease.get("expirytime"),
                        }
                    )
            except libvirt.libvirtError:
                pass
        summary["dhcp_leases"] = leases
        summary["ipam"] = network_edit.describe_xml(net.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE))
        summary["needs_restart"] = network_edit.pending(net)
        log_action(user["username"], "get_network", name, "success")
        return summary
    finally:
        conn.close()


# --- NETWORK-level firewall: distinct from the PER-VM firewall
# (app/routers/vms.py, nwfilter subsystem). This one applies to the bridge of the
# whole network (the kernel FORWARD chain); see app/core/network_firewall.py for
# the detail and for why nwfilter cannot be used at this level (checked against
# libvirt's RNG schemas). It deliberately reuses the same FirewallConfig and
# FirewallRule as the per-VM firewall: same UI, same validation, only the target
# differs. Admin-only (it touches iptables at the host level, not an individual
# VM).


@router.get("/{name}/firewall")
def get_network_firewall_route(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Network '{name}' not found") from None
        return get_network_firewall(name)
    finally:
        conn.close()


@router.put("/{name}/firewall")
def set_network_firewall(name: str, payload: FirewallConfig, user: dict = Depends(require_role("admin"))):
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
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_network_firewall", name, "failed", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{name}' not found") from None

        try:
            result = apply_network_firewall(name, payload.model_dump(), user=user["username"])
        except ValueError as e:
            log_action(user["username"], "set_network_firewall", name, "failed", str(e))
            raise HTTPException(status_code=422, detail=str(e)) from e
        except RuntimeError as e:
            log_action(user["username"], "set_network_firewall", name, "failed", str(e))
            status = 503 if isinstance(e, network_firewall.Unavailable) else 500
            raise HTTPException(status_code=status, detail=str(e)) from e

        log_action(
            user["username"],
            "set_network_firewall",
            name,
            "success",
            f"{len(payload.rules)} rule(s), bridge {result['bridge']}",
        )
        return {"message": f"Firewall applied to network '{name}' (bridge {result['bridge']})", **payload.model_dump()}
    finally:
        conn.close()


# --- Starting, stopping and autostart of a network. A network defined purpose not started (libvirt's "default" NAT
# network is shipped that way on some installations) could not be brought back from the dashboard: its VMs could
# not start and a Docker container could not be put on it. ---


def _running_vms_on(conn, network_name):
    names = []
    try:
        domains = conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)
    except libvirt.libvirtError:
        return names
    for dom in domains:
        try:
            root = ET.fromstring(dom.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError):
            continue
        if any(src.get("network") == network_name for src in root.findall(".//devices/interface/source")):
            names.append(dom.name())
    return names


def _lookup(conn, name):
    try:
        return conn.networkLookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"Network '{name}' not found") from None


@router.post("/{name}/start")
def start_network(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        net = _lookup(conn, name)
        if not net.isActive():
            _daemon(
                "network.start", user, "start_network", name, failed="The network could not start: {}", network=name
            )
            log_action(user["username"], "start_network", name, "success")
        return _network_summary(net)
    finally:
        conn.close()


def _ask(user, action, op, name, **args):
    """hyperlite-daemon's answer to `op` for the network `name`. What it refuses: 409 with its reason, as before;
    without the daemon: 503."""
    try:
        return privileged.run(op, user=user["username"], network=name, **args)
    except DaemonError as e:
        log_action(user["username"], action, name, "failed", e.detail)
        status = 409 if e.kind == "refused" else 503 if e.kind == "unavailable" else 502
        raise HTTPException(status_code=status, detail=e.detail) from None


def _daemon(op, user, action, target, refused=422, failed="{}", **args):
    """hyperlite-daemon's answer to `op` on libvirt's networks (app/daemon/ops/network.py), with the answers these
    routes gave when they wrote to libvirt themselves: what it refuses, `refused` with its reason; libvirt's failure,
    500 with `failed` around libvirt's reason; without the daemon, 503. The failure is in the audit log."""
    try:
        return privileged.run(op, user=user["username"], **args)
    except DaemonError as e:
        log_action(user["username"], action, target, "failed", e.detail)
        if e.kind in ("refused", "invalid_arguments"):
            raise HTTPException(status_code=refused, detail=e.detail) from None
        if e.kind == "unavailable":
            raise HTTPException(status_code=503, detail=e.detail) from None
        raise HTTPException(status_code=500, detail=failed.format(e.detail)) from None


class VlanAwareConfirm(BaseModel):
    token: str = Field(max_length=100)


@router.post("/{name}/vlan-aware", status_code=202)
def make_vlan_aware(name: str, user: dict = Depends(require_role("admin"))):
    """Make the network's Linux bridge VLAN-aware, as a trial: undone after `expires_in` seconds unless
    POST .../vlan-aware/confirm brings the token back, from a new request that shows the host is still reachable."""
    conn = open_conn()
    try:
        root = ET.fromstring(_lookup(conn, name).XMLDesc(0))
    finally:
        conn.close()
    if bridge_vlan.linux_bridge_of_network(root) is None:
        msg = f"Network '{name}' is not on a Linux bridge of this host"
        log_action(user["username"], "vlan_aware_bridge", name, "failed", msg)
        raise HTTPException(status_code=422, detail=msg)
    trial = _ask(user, "vlan_aware_bridge", "network.vlan_aware_start", name)
    log_action(
        user["username"], "vlan_aware_bridge", name, "success", f"bridge {trial['bridge']}: trial, to be confirmed"
    )
    return {"bridge": trial["bridge"], "token": trial["token"], "expires_in": trial["expires_in"]}


@router.post("/{name}/vlan-aware/confirm")
def confirm_vlan_aware(name: str, payload: VlanAwareConfirm, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        _lookup(conn, name)
    finally:
        conn.close()
    bridge = _ask(user, "vlan_aware_bridge", "network.vlan_aware_confirm", name, token=payload.token)["bridge"]
    log_action(user["username"], "vlan_aware_bridge", name, "success", f"bridge {bridge}: kept, set again at boot")
    return {"bridge": bridge, "vlan_aware": True}


@router.delete("/{name}/vlan-aware")
def undo_vlan_aware(name: str, user: dict = Depends(require_role("admin"))):
    """Stop filtering VLANs on a bridge Hyperlite made VLAN-aware. Refused while a VM keeps VLANs on it: they would
    become every frame of the bridge, whatever its tag."""
    conn = open_conn()
    try:
        root = ET.fromstring(_lookup(conn, name).XMLDesc(0))
        bridge = bridge_vlan.linux_bridge_of_network(root)
        on_bridge = set()
        for n in conn.listAllNetworks():
            n_bridge = ET.fromstring(n.XMLDesc(0)).find("bridge")
            if n_bridge is not None and n_bridge.get("name") == bridge:
                on_bridge.add(n.name())
        users = []
        for domain in conn.listAllDomains(0):
            dom_root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
            ports = bridge_vlan.ports_of_domain_xml(dom_root)
            for iface in dom_root.findall("devices/interface"):
                mac = iface.find("mac")
                source = iface.find("source")
                if (
                    mac is not None
                    and source is not None
                    and source.get("network") in on_bridge
                    and (mac.get("address") or "").lower() in ports
                ):
                    users.append(domain.name())
                    break
    finally:
        conn.close()
    if bridge is None:
        raise HTTPException(status_code=422, detail=f"Network '{name}' is not on a Linux bridge of this host")
    if users:
        msg = f"VMs keep VLANs on bridge {bridge}: {', '.join(sorted(users))}. Remove their VLANs first"
        log_action(user["username"], "vlan_unaware_bridge", name, "failed", msg)
        raise HTTPException(status_code=409, detail=msg)
    _ask(user, "vlan_unaware_bridge", "network.vlan_aware_off", name)
    log_action(user["username"], "vlan_unaware_bridge", name, "success", f"bridge {bridge}")
    return {"bridge": bridge, "vlan_aware": False}


@router.post("/{name}/stop")
def stop_network(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    """Stopping a network disconnects the running VMs plugged into it: they are named, and a confirmation is required."""
    conn = open_conn()
    try:
        net = _lookup(conn, name)
        if not net.isActive():
            return _network_summary(net)
        running = _running_vms_on(conn, name)
        if not confirm:
            detail = "Confirmation required (?confirm=true)"
            if running:
                detail += f": running VMs will lose this network: {', '.join(running)}"
            raise HTTPException(status_code=400, detail=detail)
        _daemon("network.stop", user, "stop_network", name, failed="The network could not stop: {}", network=name)
        log_action(user["username"], "stop_network", name, "success", ", ".join(running) or None)
        return _network_summary(net)
    finally:
        conn.close()


class AutostartSetting(BaseModel):
    autostart: bool


@router.put("/{name}/autostart")
def set_network_autostart(name: str, payload: AutostartSetting, user: dict = Depends(require_role("admin"))):
    """Whether libvirt starts the network when the host boots."""
    conn = open_conn()
    try:
        net = _lookup(conn, name)
        _daemon("network.autostart", user, "network_autostart", name, network=name, autostart=payload.autostart)
        log_action(user["username"], "network_autostart", name, "success", "on" if payload.autostart else "off")
        return _network_summary(net)
    finally:
        conn.close()


class DhcpRange(BaseModel):
    start: str
    end: str


class NetworkEdit(BaseModel):
    mode: str = Field(description="'nat' | 'isolated'")
    address: str = Field(description="Gateway address of the network on the host")
    netmask: str = "255.255.255.0"
    dhcp: DhcpRange | None = None


@router.patch("/{name}")
def edit_network(name: str, payload: NetworkEdit, user: dict = Depends(require_role("admin"))):
    """Subnet, DHCP range and NAT/isolated mode of a network Hyperlite routes itself (not a bridge onto the LAN).
    On a running network only a DHCP range change is live; the rest waits for its restart (`needs_restart`)."""
    conn = open_conn()
    try:
        _lookup(conn, name)
        restart = _daemon(
            "network.edit",
            user,
            "edit_network",
            name,
            failed="The network could not be changed: {}",
            network=name,
            mode=payload.mode,
            address=payload.address,
            netmask=payload.netmask,
            dhcp=payload.dhcp.model_dump() if payload.dhcp else None,
        )["needs_restart"]
        detail = f"{payload.mode} {payload.address}/{payload.netmask}"
        if payload.dhcp:
            detail += f" dhcp {payload.dhcp.start}-{payload.dhcp.end}"
        log_action(user["username"], "edit_network", name, "success", detail)
        return get_network(name, user) | {"needs_restart": restart}
    finally:
        conn.close()


class Reservation(BaseModel):
    mac: str
    ip: str
    name: str | None = None


@router.post("/{name}/reservations", status_code=201)
def add_reservation(name: str, payload: Reservation, user: dict = Depends(require_role("admin"))):
    """A fixed address for one MAC on the network's DHCP server, live and in its definition."""
    conn = open_conn()
    try:
        _lookup(conn, name)
        _daemon(
            "network.reservation_add",
            user,
            "add_reservation",
            name,
            failed="The reservation could not be added: {}",
            network=name,
            mac=payload.mac,
            ip=payload.ip,
            name=payload.name or None,
        )
        log_action(user["username"], "add_reservation", name, "success", f"{payload.mac} -> {payload.ip}")
        return network_edit.describe_xml(_lookup(conn, name).XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE))
    finally:
        conn.close()


@router.delete("/{name}/reservations/{mac}")
def delete_reservation(name: str, mac: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        _lookup(conn, name)
        found = _daemon(
            "network.reservation_delete",
            user,
            "delete_reservation",
            name,
            failed="The reservation could not be removed: {}",
            network=name,
            mac=mac,
        )["found"]
        if not found:
            raise HTTPException(status_code=404, detail=f"No reservation for {mac} on '{name}'")
        log_action(user["username"], "delete_reservation", name, "success", mac)
        return network_edit.describe_xml(_lookup(conn, name).XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE))
    finally:
        conn.close()


# --- Creation/deletion of virtual networks: a simplified equivalent of vSwitches
# and Port Groups. A libvirt network is the equivalent of a port group attached
# to a NAT, isolated or bridged vSwitch. Admin-only (creating a network changes
# the host's own network configuration, not just a VM). ---


class NetworkCreate(BaseModel):
    name: str
    mode: str = Field(description="'nat' | 'isolated' | 'bridge'")
    bridge_name: str | None = Field(
        None,
        description="Host interface (required if mode='bridge'): an existing bridge, or a NIC/bond/VLAN used through "
        "macvtap (see GET /networks/host-interfaces)",
    )
    subnet_address: str | None = Field(None, description="Gateway address, e.g. '192.168.150.1' (nat/isolated)")
    subnet_netmask: str = "255.255.255.0"
    dhcp_start: str | None = None
    dhcp_end: str | None = None


@router.post("", status_code=201)
def create_network(payload: NetworkCreate, user: dict = Depends(require_role("admin"))):
    name_error = validate_name(payload.name, "network")
    if name_error:
        log_action(user["username"], "create_network", payload.name, "failed", name_error)
        raise HTTPException(status_code=422, detail=name_error)

    if payload.mode not in ("nat", "isolated", "bridge"):
        raise HTTPException(status_code=422, detail="mode must be 'nat', 'isolated' or 'bridge'")

    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(payload.name)
            log_action(user["username"], "create_network", payload.name, "failed", "already exists")
            raise HTTPException(status_code=409, detail=f"A network '{payload.name}' already exists")
        except libvirt.libvirtError:
            pass

        fields = payload.model_dump(exclude={"name"})
        try:
            # Checked here for the answer; hyperlite-daemon checks the same fields again and writes the definition.
            network_create.network_xml(payload.name, **fields)
            if payload.mode != "bridge":
                conflict = _subnet_conflict(
                    conn, network_create.subnet_of(payload.subnet_address, payload.subnet_netmask)
                )
                if conflict:
                    log_action(user["username"], "create_network", payload.name, "failed", conflict)
                    raise HTTPException(status_code=409, detail=f"{conflict}: choose another address range")
        except network_create.SpecError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        _daemon(
            "network.create",
            user,
            "create_network",
            payload.name,
            failed="Network creation error: {}",
            name=payload.name,
            **fields,
        )
        net = conn.networkLookupByName(payload.name)

        log_action(user["username"], "create_network", payload.name, "success")
        return _network_summary(net)
    finally:
        conn.close()


class NetworkRename(BaseModel):
    new_name: str


def _guests_on(conns, network_name):
    """(running, stopped) guests of these connections (VMs, containers) with an interface on the network."""
    running, stopped = [], []
    for conn in conns:
        for dom in conn.listAllDomains():
            try:
                root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
            except (libvirt.libvirtError, ET.ParseError):
                continue
            if any(
                s.get("network") == network_name for s in root.findall(".//devices/interface[@type='network']/source")
            ):
                (running if dom.isActive() else stopped).append((conn, dom, root))
    return running, stopped


@router.post("/{name}/rename")
def rename_network(name: str, payload: NetworkRename, user: dict = Depends(require_role("admin"))):
    """libvirt cannot rename a network: it is redefined under the new name (same bridge, subnet, DHCP range and
    reservations), and the VMs and containers attached to it point at the new name. Refused while one of them runs:
    its running interface would stay on a network that no longer exists."""
    new = payload.new_name
    error = validate_name(new, "network")
    if error:
        raise HTTPException(status_code=422, detail=error)
    if name in ("default", "hyperlite-isolated"):
        raise HTTPException(
            status_code=403, detail=f"Network '{name}' is a system network: Hyperlite relies on its name"
        )
    if new == name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    conn = open_conn()
    lxc = open_lxc_conn_if_any()
    try:
        _lookup(conn, name)
        try:
            conn.networkLookupByName(new)
            raise HTTPException(status_code=409, detail=f"A network named '{new}' already exists")
        except libvirt.libvirtError:
            pass
        running, _stopped = network_edit.guests_on([c for c in (conn, lxc) if c is not None], name)
        if running:
            raise HTTPException(
                status_code=409,
                detail=f"Running guests use this network ({', '.join(d.name() for _c, d, _r in running)}): stop "
                "them first",
            )
    finally:
        conn.close()
        if lxc is not None:
            lxc.close()
    firewall = get_network_firewall(name)
    try:
        remove_network_firewall(name, user=user["username"])
    except (ValueError, RuntimeError):
        # The rename goes on: the old chain only matches a bridge that keeps its name, and it is rebuilt below.
        logger.warning("Removing the firewall of network %s before its rename failed", name, exc_info=True)
    moved = _daemon(
        "network.rename",
        user,
        "rename_network",
        name,
        refused=409,
        failed="Rename failed: {}",
        network=name,
        new_name=new,
    )["guests_updated"]
    renaming.network_records(name, new)
    if firewall["rules"] or firewall["default_policy"] != "accept":
        try:
            apply_network_firewall(new, firewall, user=user["username"])
        except (ValueError, RuntimeError) as e:
            log_action(user["username"], "rename_network", new, "failed", f"firewall not reapplied: {e}")
    log_action(user["username"], "rename_network", name, "success", f"-> {new} ({len(moved)} guest(s) moved over)")
    conn = open_conn()
    try:
        return {**_network_summary(conn.networkLookupByName(new)), "guests_updated": moved}
    finally:
        conn.close()


@router.delete("/{name}")
def delete_network(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if name in ("default", "hyperlite-isolated"):
        raise HTTPException(status_code=403, detail=f"Network '{name}' is a system network and cannot be deleted")

    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_network", name, "failed", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{name}' not found") from None

        # Refuse if a VM (running or not) still has an interface on this network:
        # deleting it from under the VM would break its connectivity at the next start
        # with no clear error message for the user.
        attached_vms = []
        for domain in conn.listAllDomains():
            try:
                root = ET.fromstring(domain.XMLDesc(0))
            except libvirt.libvirtError:
                continue
            for source in root.findall(".//devices/interface[@type='network']/source"):
                if source.get("network") == name:
                    attached_vms.append(domain.name())
                    break
        if attached_vms:
            log_action(user["username"], "delete_network", name, "failed", f"in use by {attached_vms}")
            raise HTTPException(
                status_code=409,
                detail=f"Network in use by: {', '.join(attached_vms)}. Detach these interfaces before deleting it",
            )

        if not confirm:
            log_action(user["username"], "delete_network", name, "failed", "Confirmation missing")
            raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")

        # Clean up the network firewall BEFORE destroying the network:
        # remove_network_firewall needs to read the bridge from the libvirt XML that is
        # still in place to cleanly remove the jump from HYPERLITENETFW.
        try:
            remove_network_firewall(name, user=user["username"])
        except (ValueError, RuntimeError):
            logger.warning("Removing the firewall of network %s failed; the deletion goes on", name, exc_info=True)

        _daemon("network.delete", user, "delete_network", name, refused=409, failed="Deletion error: {}", network=name)

        log_action(user["username"], "delete_network", name, "success")
        return {"message": f"Network '{name}' deleted"}
    finally:
        conn.close()
