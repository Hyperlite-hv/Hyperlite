"""The cluster's keys (docs/design/privilege-separation.md, section 13, the cluster domain, part 1): the key pair this
node reaches the others with, root's known_hosts and the reverse trust a node is given into this one
(app/core/cluster.py). Only hyperlite-daemon reads the private keys and writes root's authorized_keys; the API names a
registered node, whose address the daemon reads from the node registry, or the address of a node being checked
before it is registered."""

from typing import Literal

from pydantic import Field, field_validator

from app.daemon.needs import ANYONE
from app.daemon.registry import Args, DaemonError, operation

NODE_NAME = 63


def _registered(name):
    from app.core import cluster

    node = cluster.get_node(name)
    if node is None:
        raise DaemonError("not_found", f"Node '{name}' not found")
    return node


@operation("cluster.pubkey", Args)
def pubkey(args, ctx):
    """The cluster's public key (its pair made the first time), to authorize on a node before it is registered."""
    from app.core import cluster

    return {"public_key": cluster.get_cluster_pubkey()}


class AddressArgs(Args):
    hostname: str = Field(min_length=1, max_length=254)
    ssh_user: str = Field(min_length=1, max_length=32)
    ssh_port: int = Field(ge=1, le=65535)


@operation("cluster.test_node", AddressArgs)
def test_node(args, ctx):
    """A real libvirt connection over SSH with the cluster's key, and a QEMU/KVM host behind it: {"ok", "detail"}, the
    detail libvirt's raw reason when it failed (the route keeps it for the audit log)."""
    from app.core import cluster

    try:
        cluster.check_address(args.hostname, args.ssh_user)
    except ValueError as e:
        raise DaemonError("refused", str(e)) from None
    ok, detail = cluster.test_node_connection(args.hostname, args.ssh_user, args.ssh_port)
    return {"ok": ok, "detail": detail}


class NodeArgs(Args):
    node: str = Field(min_length=1, max_length=NODE_NAME)


@operation("cluster.trust", NodeArgs)
def trust(args, ctx):
    """A newly registered node: its host key recorded (trust on first use), and the reverse trust given (its own key
    pair, pushed to it, authorized here from its address only). The reverse trust is best-effort, as before: its
    failure is answered, the registration stands. {"reverse_error"}."""
    from app.core import cluster

    node = _registered(args.node)
    cluster.ensure_known_host(node)
    try:
        cluster.ensure_reverse_trust(node)
    except Exception as e:  # the reverse direction is a secondary feature: said, never fatal
        return {"reverse_error": str(e)}
    return {"reverse_error": None}


@operation("cluster.forget", NodeArgs)
def forget(args, ctx):
    """A node being removed (still registered when this is asked): its recorded host key and its reverse trust go."""
    from app.core import cluster

    node = _registered(args.node)
    cluster.forget_known_host(node)
    cluster.revoke_reverse_trust(node["name"])


class RenameArgs(NodeArgs):
    new_name: str = Field(min_length=1, max_length=NODE_NAME)


@operation("cluster.rename_trust", RenameArgs)
def rename_trust(args, ctx):
    """A renamed node keeps its reverse key: its files and its authorized_keys marker take the new name."""
    from app.core import cluster

    for name in (args.node, args.new_name):
        if not cluster.NODE_NAME_RE.fullmatch(name):
            raise DaemonError("refused", "Invalid node name")
    cluster.rename_reverse_trust(args.node, args.new_name)


@operation("node.tunnel", NodeArgs, needs=ANYONE)
def tunnel(args, ctx):
    """The tunnel to a registered node's read-only libvirt, opened when it is not (app/core/node_tunnel.py), for the
    API's reads of that node: {"socket"}."""
    from app.core import node_health, node_tunnel

    node = _registered(args.node)
    try:
        node_health.check(node)
        return {"socket": str(node_tunnel.ensure(node))}
    except node_health.NodeDown as e:
        raise DaemonError("unavailable", f"Node '{args.node}' is down") from e
    except (OSError, ValueError) as e:
        raise DaemonError("unreachable", str(e)) from e


class HookscriptsArgs(NodeArgs):
    vm: str | None = Field(default=None, min_length=1, max_length=64)


@operation("node.hookscripts", HookscriptsArgs, needs=ANYONE)
def hookscripts(args, ctx):
    """A node's library of hook scripts and, `vm`, that VM's last runs there: read over SSH with the cluster's key
    (app/core/hookscript.py). {"library", "runs"}."""
    import subprocess

    from app.core import hookscript

    _registered(args.node)
    try:
        return {
            "library": hookscript.list_scripts_on(args.node),
            "runs": hookscript.runs_on(args.node, args.vm) if args.vm else None,
        }
    except (hookscript.HookError, OSError, ValueError, subprocess.SubprocessError) as e:
        raise DaemonError("unreachable", f"The node's hook scripts could not be read: {e}") from e


@operation("node.capabilities", NodeArgs, needs=ANYONE)
def capabilities(args, ctx):
    """A registered node's capability profile (host_capabilities.get_remote_capabilities): its libvirt opened, and a
    fixed probe run over SSH, with the cluster's key."""
    import libvirt

    from app.core import host_capabilities
    from app.core.error_messages import describe_exception

    _registered(args.node)
    try:
        return host_capabilities.get_remote_capabilities(args.node)
    except libvirt.libvirtError as e:
        raise DaemonError("unreachable", f"Node unreachable: {describe_exception(e)}") from e


# --- Part 4: the copies and the commands between nodes ---

ISO_NAME = 205


class IsoNodeArgs(NodeArgs):
    name: str = Field(min_length=1, max_length=ISO_NAME)


@operation("iso.list", NodeArgs, needs=ANYONE)
def iso_list(args, ctx):
    """Another node's ISO library, read there over SSH (iso_share): {"isos"}; not_found when it has none."""
    from app.core import iso_share

    try:
        return {"isos": iso_share._remote_isos(_registered(args.node))}
    except iso_share.NoLibrary:
        raise DaemonError("not_found", iso_share.NO_LIBRARY) from None
    except (OSError, RuntimeError, ValueError) as e:
        raise DaemonError("unreachable", str(e)) from e


@operation("iso.size", IsoNodeArgs, needs=ANYONE)
def iso_size(args, ctx):
    """An image's size in another node's library, None when it is not there: {"size"}."""
    from app.core import iso_share

    try:
        return {"size": iso_share._remote_size(_registered(args.node), args.name)}
    except (OSError, ValueError) as e:
        raise DaemonError("unreachable", str(e)) from e


@operation("iso.delete_remote", IsoNodeArgs)
def iso_delete_remote(args, ctx):
    from app.core import iso_share

    if not iso_share.valid_iso_name(args.name):
        raise DaemonError("refused", f"Invalid ISO name '{args.name}'")
    try:
        iso_share._delete_remote(_registered(args.node), args.name)
    except (OSError, RuntimeError, ValueError) as e:
        raise DaemonError("failed", str(e)) from e


class IsoCopyArgs(Args):
    name: str = Field(min_length=1, max_length=ISO_NAME)
    source: str | None = Field(None, min_length=1, max_length=NODE_NAME)
    target: str | None = Field(None, min_length=1, max_length=NODE_NAME)
    task_id: str = Field(min_length=1, max_length=64)
    total: int = Field(ge=0)


# One call that waits: an image copied between two nodes, through this one when neither is it.
@operation("iso.copy", IsoCopyArgs)
def iso_copy(args, ctx):
    """An image copied from one node's library to another's (one of them may be this node), over SSH with the
    cluster's key, its progress written into the task (iso_share._copy)."""
    from app.core import iso_share

    if not iso_share.valid_iso_name(args.name):
        raise DaemonError("refused", f"Invalid ISO name '{args.name}'")
    if args.source == args.target:
        raise DaemonError("refused", "The source node cannot also be a target")
    source = None if args.source is None else _registered(args.source)
    target = None if args.target is None else _registered(args.target)
    try:
        iso_share._copy(args.name, source, target, args.task_id, args.total)
    except (OSError, RuntimeError, ValueError) as e:
        raise DaemonError("failed", str(e)) from e


class ConfigCopyArgs(Args):
    nodes: list[str] = Field(min_length=1, max_length=256)


@operation("config.copy", ConfigCopyArgs)
def config_copy(args, ctx):
    """The cluster's configuration (a database snapshot and the node's keys) sent to registered nodes over SSH with
    the cluster's key (config_copy.send): one result per node."""
    from app.core import cluster, config_copy

    for name in args.nodes:
        if not cluster.NODE_NAME_RE.fullmatch(name):
            raise DaemonError("refused", "Invalid node name")
    return config_copy.send(args.nodes)


@operation("node.facts", NodeArgs, needs=ANYONE)
def facts(args, ctx):
    """A registered node's boot id and host name, read over SSH (rolling_update.read_facts)."""
    from app.core import rolling_update

    node = _registered(args.node)
    try:
        return rolling_update.read_facts(node)
    except (OSError, ValueError) as e:
        raise DaemonError("unreachable", str(e)) from e


class PeerCallArgs(NodeArgs):
    # A call to the member's own local API, its ticket inside: the member checks the user's rights as for any call.
    request: dict
    connect_timeout: int = Field(10, ge=1, le=60)

    @field_validator("request")
    @classmethod
    def _call(cls, request):
        from app.core import rolling_update

        if set(request) != {"url", "method", "body", "timeout", "token"}:
            raise ValueError("unexpected request fields")
        if not str(request["url"]).startswith(rolling_update.API_URL + "/"):
            raise ValueError("only the member's own API")
        if request["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("unexpected method")
        if not isinstance(request["timeout"], int | float) or not 0 < request["timeout"] <= 7200:
            raise ValueError("unexpected timeout")
        return request


@operation("node.peer_call", PeerCallArgs)
def peer_call(args, ctx):
    """_peer_api's fallback when HTTPS cannot be used: the fixed script run on the member over SSH with the cluster's
    key (rolling_update.call_over_ssh). {"status", "body"}."""
    from app.core import rolling_update

    node = _registered(args.node)
    try:
        status, body = rolling_update.call_over_ssh(node, args.request, args.connect_timeout)
    except rolling_update.PeerUnreachable as e:
        raise DaemonError("unreachable", str(e)) from e
    return {"status": status, "body": body}


class ProbeArgs(NodeArgs):
    probe: Literal["stats", "hardware"]


@operation("node.probe", ProbeArgs, needs=ANYONE)
def probe(args, ctx):
    """One of Hyperlite's fixed read-only scripts run on a registered node over SSH (host_stats): {"output"}, None
    when the node did not answer."""
    from app.core import host_stats

    return {"output": host_stats.run_script_on(host_stats._probes()[args.probe], _registered(args.node))}


# ---- The cluster's status (app/core/statd.py): this node's summary sent at once, for an API that is not root ----

STATD_MIN_S = 1.0  # at most one summary a second, however many accounts ask
_statd_last = [0.0]


@operation("statd.publish", needs=ANYONE)
def statd_publish(args, ctx):
    """This node's summary sent now (hyperlite-cfs takes it from root only), after a change any account made through
    the API: {"sent": bytes or None}. Asked again within STATD_MIN_S, nothing is sent: the one just sent is current."""
    import time

    from app.core import statd

    now = time.monotonic()
    if now - _statd_last[0] < STATD_MIN_S:
        return {"sent": None}
    _statd_last[0] = now
    return {"sent": statd.publish()}
