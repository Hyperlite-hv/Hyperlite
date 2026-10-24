"""The network's root work (docs/design/privilege-separation.md, section 13, the network domain, part 1): the iptables
of the network firewall (app/core/network_firewall.py) and the VLANs of Linux bridges (app/core/bridge_vlan.py).

Each operation takes a network's or a VM's name and reads its bridge or its ports itself, from libvirt: never an
interface name from the API, which could otherwise point a rule or a VLAN change at the host's own uplink. The routes
keep who may ask, the audit log and the database."""

import functools
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import bridge_vlan, network_firewall
from app.daemon.registry import Args, DaemonError, NoArgs, operation

NAME = 64  # a libvirt network's or VM's name, only ever looked up
RULES = 256


def _refusals(fn):
    """What the modules refuse becomes the daemon's "refused", their message for the user; iptables refusing a rule,
    a "failed" with its reason."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        try:
            return fn(args, ctx)
        except (network_firewall.Refused, bridge_vlan.VlanError) as e:
            raise DaemonError("refused", str(e)) from e
        except RuntimeError as e:
            raise DaemonError("failed", str(e)) from e

    return wrapper


class FirewallRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["in", "out", "inout"]
    protocol: Literal["tcp", "udp", "icmp", "all"]
    port: int | None = Field(None, ge=1, le=65535)
    action: Literal["accept", "drop"]


class FirewallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_policy: Literal["accept", "drop"]
    rules: list[FirewallRule] = Field(max_length=RULES)


class NetworkArgs(Args):
    network: str = Field(min_length=1, max_length=NAME)


class FirewallArgs(NetworkArgs):
    config: FirewallConfig


@operation("network.firewall_apply", FirewallArgs)
@_refusals
def firewall_apply(args, ctx):
    return network_firewall.apply_rules(args.network, args.config.model_dump())


@operation("network.firewall_remove", NetworkArgs)
@_refusals
def firewall_remove(args, ctx):
    network_firewall.remove_rules(args.network)


@operation("network.vlan_aware_start", NetworkArgs)
@_refusals
def vlan_aware_start(args, ctx):
    """The network's Linux bridge made VLAN-aware as a trial, undone by this daemon unless confirmed in time."""
    bridge = bridge_vlan.network_bridge(args.network)
    return {"bridge": bridge, "token": bridge_vlan.start_trial(bridge), "expires_in": bridge_vlan.TRIAL_S}


class ConfirmArgs(NetworkArgs):
    token: str = Field(min_length=1, max_length=100)


@operation("network.vlan_aware_confirm", ConfirmArgs)
@_refusals
def vlan_aware_confirm(args, ctx):
    bridge = bridge_vlan.network_bridge(args.network)
    bridge_vlan.confirm_trial(bridge, args.token)
    return {"bridge": bridge}


@operation("network.vlan_aware_off", NetworkArgs)
@_refusals
def vlan_aware_off(args, ctx):
    bridge = bridge_vlan.network_bridge(args.network)
    bridge_vlan.turn_off(bridge)
    return {"bridge": bridge}


class VmArgs(Args):
    vm: str = Field(min_length=1, max_length=NAME)


@operation("network.vlan_apply_vm", VmArgs)
@_refusals
def vlan_apply_vm(args, ctx):
    """A running VM's VLANs set on its bridge ports. Returns the errors, one per port (each such port then carries no
    traffic)."""
    return {"errors": bridge_vlan.apply_vm(args.vm)}


@operation("network.vlan_boot", NoArgs)
@_refusals
def vlan_boot(args, ctx):
    """What the service does when it starts: the bridges kept VLAN-aware made so again (where the boot unit has not
    run yet), and the VLANs of the VMs already running."""
    return {"errors": bridge_vlan.boot() + bridge_vlan.apply_running()}


# --- Part 2a: libvirt's networks (define, start, stop, autostart, edit, rename, delete) ---


def _connect():
    import libvirt

    from app.core.libvirt_utils import LIBVIRT_URI

    return libvirt.open(LIBVIRT_URI)


def _libvirt(fn):
    """A libvirt failure becomes the daemon's "failed" with libvirt's own reason, which the routes show as before; what
    the modules refuse, its "refused"."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        import libvirt

        from app.core import network_create, network_edit
        from app.core.error_messages import describe_exception

        try:
            return fn(args, ctx)
        except (network_create.SpecError, network_edit.EditError) as e:
            raise DaemonError("refused", str(e)) from e
        except libvirt.libvirtError as e:
            raise DaemonError("failed", describe_exception(e)) from e

    return wrapper


def _network(conn, name):
    import libvirt

    try:
        return conn.networkLookupByName(name)
    except libvirt.libvirtError:
        raise DaemonError("refused", f"Network '{name}' not found") from None


def _check_name(name):
    """A name the daemon writes into a definition: the API's own rule (letters, digits and dashes), checked again."""
    from app.core.vm_builder import validate_name

    error = validate_name(name, "network")
    if error:
        raise DaemonError("refused", error)


# Bounded, but loose enough that a mistyped address gets the modules' own message (network_create, network_edit).
ADDRESS = 64
IPV4 = Field(None, max_length=ADDRESS)


class CreateArgs(Args):
    name: str = Field(min_length=1, max_length=NAME)
    mode: Literal["nat", "isolated", "bridge"]
    bridge_name: str | None = Field(None, max_length=15)
    subnet_address: str | None = IPV4
    subnet_netmask: str = Field("255.255.255.0", max_length=ADDRESS)
    dhcp_start: str | None = IPV4
    dhcp_end: str | None = IPV4


@operation("network.create", CreateArgs)
@_libvirt
def create(args, ctx):
    """Defines, starts and autostarts a network from checked fields (app/core/network_create.py); a network defined but
    not started is undefined again."""
    import libvirt

    from app.core import network_create

    _check_name(args.name)
    fields = args.model_dump()
    xml = network_create.network_xml(fields.pop("name"), **fields)
    conn = _connect()
    try:
        try:
            conn.networkLookupByName(args.name)
            raise DaemonError("refused", f"A network '{args.name}' already exists")
        except libvirt.libvirtError:
            pass
        net = conn.networkDefineXML(xml)
        try:
            net.create()
            net.setAutostart(True)
        except libvirt.libvirtError:
            try:
                if net.isActive():
                    net.destroy()
                net.undefine()
            except libvirt.libvirtError:
                pass
            raise
    finally:
        conn.close()


@operation("network.start", NetworkArgs)
@_libvirt
def start(args, ctx):
    conn = _connect()
    try:
        net = _network(conn, args.network)
        if not net.isActive():
            net.create()
    finally:
        conn.close()


@operation("network.stop", NetworkArgs)
@_libvirt
def stop(args, ctx):
    conn = _connect()
    try:
        net = _network(conn, args.network)
        if net.isActive():
            net.destroy()
    finally:
        conn.close()


class AutostartArgs(NetworkArgs):
    autostart: bool


@operation("network.autostart", AutostartArgs)
@_libvirt
def autostart(args, ctx):
    conn = _connect()
    try:
        _network(conn, args.network).setAutostart(1 if args.autostart else 0)
    finally:
        conn.close()


class DhcpRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(max_length=ADDRESS)
    end: str = Field(max_length=ADDRESS)


class EditArgs(NetworkArgs):
    mode: Literal["nat", "isolated"]
    address: str = Field(max_length=ADDRESS)
    netmask: str = Field(max_length=ADDRESS)
    dhcp: DhcpRange | None = None


@operation("network.edit", EditArgs)
@_libvirt
def edit(args, ctx):
    """Subnet, DHCP range and mode of a routed network (app/core/network_edit.apply_edit, which checks them)."""
    from app.core import network_edit

    conn = _connect()
    try:
        restart = network_edit.apply_edit(
            conn,
            _network(conn, args.network),
            mode=args.mode,
            address=args.address,
            netmask=args.netmask,
            dhcp=args.dhcp.model_dump() if args.dhcp else None,
        )
    finally:
        conn.close()
    return {"needs_restart": restart}


class RenameArgs(NetworkArgs):
    new_name: str = Field(min_length=1, max_length=NAME)


@operation("network.rename", RenameArgs)
@_libvirt
def rename(args, ctx):
    from app.core import network_edit
    from app.core.libvirt_utils import open_lxc_conn_if_any

    _check_name(args.new_name)
    conn = _connect()
    lxc = open_lxc_conn_if_any()
    try:
        _network(conn, args.network)
        return {"guests_updated": network_edit.rename(conn, lxc, args.network, args.new_name)}
    finally:
        conn.close()
        if lxc is not None:
            lxc.close()


@operation("network.delete", NetworkArgs)
@_libvirt
def delete(args, ctx):
    """Stops and undefines a network no VM uses: the daemon checks both again, the system networks never."""
    from app.core import network_edit

    if args.network in network_edit.SYSTEM_NETWORKS:
        raise DaemonError("refused", f"Network '{args.network}' is a system network and cannot be deleted")
    conn = _connect()
    try:
        net = _network(conn, args.network)
        running, stopped = network_edit.guests_on([conn], args.network)
        if running or stopped:
            names = ", ".join(sorted(d.name() for _c, d, _r in running + stopped))
            raise DaemonError("refused", f"Network in use by: {names}. Detach these interfaces before deleting it")
        if net.isActive():
            net.destroy()
        net.undefine()
    finally:
        conn.close()


@operation("network.ensure_isolated", NoArgs)
@_libvirt
def ensure_isolated(args, ctx):
    """The isolated network Hyperlite relies on (libvirt_utils.ensure_isolated_network), defined and started."""
    from app.core.libvirt_utils import ensure_isolated_network

    conn = _connect()
    try:
        ensure_isolated_network(conn)
    finally:
        conn.close()
