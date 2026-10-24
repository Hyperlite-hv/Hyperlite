"""The cluster's keys (docs/design/privilege-separation.md, section 13, the cluster domain, part 1): the key pair this
node reaches the others with, root's known_hosts and the reverse trust a node is given into this one
(app/core/cluster.py). Only hyperlite-daemon reads the private keys and writes root's authorized_keys; the API names a
registered node, whose address the daemon reads from the node registry, or the address of a node being checked
before it is registered."""

from pydantic import Field

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


@operation("node.tunnel", NodeArgs)
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


@operation("node.hookscripts", HookscriptsArgs)
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


@operation("node.capabilities", NodeArgs)
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
