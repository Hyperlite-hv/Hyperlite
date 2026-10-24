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
