"""The consoles and the shells (docs/design/privilege-separation.md, section 13, the consoles domain): each session
needs root, a root terminal, the cluster's key, the automation key or the node's SPICE setup, so the daemon opens it
and hands its descriptor to the API (app/core/console_sessions.py). An operation names the guest and its node; the
daemon reads the display's port from the definition, the guest's address from libvirt and its account from the
records, never a port, an address or a command from the API. The API keeps the tickets, who may open what, the audit
log and the bridge to the browser."""

import ipaddress
import re
from typing import Literal

from pydantic import Field

from app.daemon.registry import Args, DaemonError, Handover, operation

VM_NAME = 64
CONTAINER_NAME = 64
NODE = Field(default=None, min_length=1, max_length=63)
# The account the automation key signs in as (vm_meta, container_meta): a plain user name, never an ssh option.
_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def _node(name):
    """The registered node a session reaches (None: this one)."""
    if not name or name == "local":
        return None
    from app.core import cluster, node_health

    row = cluster.get_node(name)
    if row is None:
        raise DaemonError("not_found", f"Node '{name}' not found")
    try:
        node_health.check(row)
    except node_health.NodeDown as e:
        raise DaemonError("unavailable", e.detail) from None
    return row


def _vm(conn, name):
    import libvirt

    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        raise DaemonError("not_found", f"VM '{name}' not found") from None
    if not domain.isActive():
        raise DaemonError("refused", f"VM '{name}' is not running")
    return domain


def _container(conn, name):
    import libvirt

    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        raise DaemonError("not_found", f"Container '{name}' not found") from None
    if not domain.isActive():
        raise DaemonError("refused", "The container must be running to open a terminal")
    return domain


def _lease_ipv4(domain):
    import libvirt

    try:
        for iface in domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE).values():
            for addr in iface.get("addrs", []):
                if addr.get("type") == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                    return addr.get("addr")
    except libvirt.libvirtError:
        return None
    return None


def _address(address):
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        raise DaemonError("refused", "The guest's address is not known yet (no DHCP lease yet?)") from None


def _account(user, what):
    if not user or not _ACCOUNT.fullmatch(user):
        raise DaemonError("refused", f"No known SSH user for '{what}'")
    return user


class SessionArgs(Args):
    cols: int = Field(80, ge=1, le=1000)
    rows: int = Field(24, ge=1, le=1000)


@operation("console.host_shell", SessionArgs)
def host_shell(args, ctx):
    """A root login shell on this host, in a new terminal: {"session"} and the terminal."""
    from app.core import console_sessions

    env = console_sessions.root_environment()
    fd, sid = console_sessions.open_pty([env["SHELL"], "-l"], env, args.cols, args.rows)
    return Handover({"session": sid}, [fd])


class ContainerArgs(SessionArgs):
    container: str = Field(min_length=1, max_length=CONTAINER_NAME)


@operation("console.container_shell", ContainerArgs)
def container_shell(args, ctx):
    """A root shell inside a running container: nsenter into the namespaces of its PID 1, which the daemon finds
    from libvirt's controller (app/core/container_shell.py)."""
    from app.core import console_sessions, container_shell, libvirt_utils
    from app.core.container_meta import get_container_app

    with libvirt_utils.local_lxc_conn() as conn:
        domain = _container(conn, args.container)
        pid = container_shell.init_pid(domain.ID())
    if pid is None:
        raise DaemonError("refused", "The container must be running to open a shell")
    app = get_container_app(args.container)
    env = container_shell.environment(app["spec"] if app else None)
    fd, sid = console_sessions.open_pty(container_shell.command(pid), env, args.cols, args.rows)
    return Handover({"session": sid}, [fd])


@operation("console.container_terminal", ContainerArgs)
def container_terminal(args, ctx):
    """ssh into a running system container as its account, with the automation key: {"session", "address", "user"}
    and the terminal."""
    from app.core import console_sessions, libvirt_utils
    from app.core.container_meta import get_container_app, get_container_ssh_user

    if get_container_app(args.container):
        raise DaemonError("refused", "An application container runs only its image's process, with no SSH server")
    with libvirt_utils.local_lxc_conn() as conn:
        address = _address(_lease_ipv4(_container(conn, args.container)))
    user = _account(get_container_ssh_user(args.container), args.container)
    argv = console_sessions.guest_ssh(address, user)
    fd, sid = console_sessions.open_pty(argv, console_sessions.root_environment(), args.cols, args.rows)
    return Handover({"session": sid, "address": address, "user": user}, [fd])


class VmArgs(SessionArgs):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    node: str | None = NODE


@operation("console.terminal", VmArgs)
def terminal(args, ctx):
    """ssh into a running VM as its cloud-init account, with the automation key, through its node for a VM of another
    node: {"session", "address", "user"} and the terminal. The address is the one the VM page shows: the guest
    agent's, else the DHCP lease."""
    from app.core import console_sessions, guest_agent, libvirt_utils
    from app.core.vm_meta import get_vm_ssh_user

    node = _node(args.node)
    with libvirt_utils.node_conn(args.node) as conn:
        domain = _vm(conn, args.vm)
        address = _address(guest_agent.ipv4(domain) or _lease_ipv4(domain))
    user = _account(get_vm_ssh_user(args.vm), args.vm)
    argv = console_sessions.guest_ssh(address, user, node)
    fd, sid = console_sessions.open_pty(argv, console_sessions.root_environment(), args.cols, args.rows)
    return Handover({"session": sid, "address": address, "user": user}, [fd])


@operation("console.serial", VmArgs)
def serial(args, ctx):
    """`virsh console` on a running VM's first serial port, the VM named by the UUID libvirt gave it (no name can pass
    for an option of virsh), on its node."""
    from app.core import console_sessions, libvirt_utils
    from app.core.cluster import libvirt_uri_from_here

    node = _node(args.node)
    with libvirt_utils.node_conn(args.node) as conn:
        uuid = _vm(conn, args.vm).UUIDString()
    uri = libvirt_uri_from_here(node) if node else libvirt_utils.LIBVIRT_URI
    # virsh prints its own messages in the user's language: C.UTF-8 keeps them English and the guest's UTF-8 intact.
    env = console_sessions.root_environment(LC_ALL="C.UTF-8")
    argv = ["virsh", "--quiet", "--connect", uri, "console", "--domain", uuid]
    fd, sid = console_sessions.open_pty(argv, env, args.cols, args.rows)
    return Handover({"session": sid}, [fd])


class DisplayArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    node: str | None = NODE
    protocol: Literal["vnc", "spice"]


def display_port(xml, protocol):
    """The port QEMU serves the running VM's display on: VNC's, or SPICE's TLS port (QEMU serves SPICE in TLS only)."""
    import xml.etree.ElementTree as ET

    from app.core import spice

    if protocol == "spice":
        return spice.tls_port(xml)
    devices = ET.fromstring(xml).find("devices")
    graphics = devices.find("graphics[@type='vnc']") if devices is not None else None
    port = graphics.get("port") if graphics is not None else None
    return int(port) if port and port.isdigit() and int(port) > 0 else None


@operation("console.display", DisplayArgs)
def display(args, ctx):
    """A socket connected to a running VM's VNC or SPICE display on 127.0.0.1 of its node: a connection on this node,
    `ssh -W` with the cluster's key to another. {"session"} (None on this node) and the socket."""
    from app.core import console_sessions, libvirt_utils

    node = _node(args.node)
    with libvirt_utils.node_conn(args.node) as conn:
        port = display_port(_vm(conn, args.vm).XMLDesc(0), args.protocol)
    if port is None:
        raise DaemonError("refused", f"VM '{args.vm}' runs without a {args.protocol.upper()} display")
    try:
        if node is None:
            return Handover({"session": None}, [console_sessions.connect_local(port)])
        fd, sid = console_sessions.open_relay(console_sessions.display_relay(node, port))
    except OSError as e:
        raise DaemonError("unreachable", f"The VM's display cannot be reached: {e}") from e
    return Handover({"session": sid}, [fd])


class CloseArgs(Args):
    session: str = Field(pattern=r"^[0-9a-f]{32}$")


@operation("console.close", CloseArgs)
def close(args, ctx):
    """End a session: its program's whole process group. {"exit"}: its exit status, None for a session this daemon
    does not hold."""
    from app.core import console_sessions

    return {"exit": console_sessions.close(args.session)}


class NodeArgs(Args):
    node: str | None = NODE


@operation("console.spice_tls", NodeArgs)
def spice_tls(args, ctx):
    """The CA certificate and the server's subject of a node's SPICE in TLS, which the .vv carries (spice.node_tls:
    qemu.conf is root's only, another node is read with the cluster's key). {"ca", "subject"}."""
    from app.core import spice

    if args.node and args.node != "local":
        from app.core import cluster

        if cluster.get_node(args.node) is None:
            raise DaemonError("not_found", f"Node '{args.node}' not found")
    try:
        ca, subject = spice.node_tls(args.node if args.node != "local" else None)
    except spice.SpiceUnavailable as e:
        raise DaemonError("refused", str(e)) from None
    return {"ca": ca, "subject": subject}


class AddConsoleArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    node: str | None = NODE
    kind: Literal["vnc", "serial", "spice"]


@operation("vm.add_console", AddConsoleArgs)
def add_console(args, ctx):
    """A console the VM lacks, added to its definition: VNC to a stopped VM (a running one cannot take it), a serial
    port or a SPICE display (effective at its next start; SPICE only on a node set up for it in TLS, or the VM would no
    longer start there). {"added"}: False when the VM already has it."""
    import libvirt

    from app.core import libvirt_utils, spice
    from app.core.error_messages import describe_exception

    try:
        with libvirt_utils.node_conn(args.node) as conn:
            try:
                domain = conn.lookupByName(args.vm)
            except libvirt.libvirtError:
                raise DaemonError("not_found", f"VM '{args.vm}' not found") from None
            stored = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
            if args.kind == "vnc":
                return {"added": libvirt_utils.ensure_vnc_graphics(conn, domain)}
            if args.kind == "serial":
                if libvirt_utils.has_serial(stored):
                    return {"added": False}
                libvirt_utils.add_serial_port(conn, domain)
                return {"added": True}
            if spice.spice_graphics(stored) is not None:
                return {"added": False}
            try:
                spice.node_tls(args.node if args.node != "local" else None)
            except spice.SpiceUnavailable as e:
                raise DaemonError("refused", f"{e}: the package sets it up (scripts/ensure-spice-tls.sh)") from None
            spice.add_display(conn, domain)
            return {"added": True}
    except libvirt.libvirtError as e:
        raise DaemonError("failed", describe_exception(e)) from e


class SpicePasswordArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    node: str | None = NODE


@operation("vm.spice_password", SpicePasswordArgs)
def spice_password(args, ctx):
    """A new SPICE password on the running VM, valid a few seconds (spice.set_ticket_password: the secure definition
    and a live change, neither on a read-only connection). {"password"}."""
    import libvirt

    from app.core import libvirt_utils, spice
    from app.core.error_messages import describe_exception

    try:
        with libvirt_utils.node_conn(args.node) as conn:
            return {"password": spice.set_ticket_password(_vm(conn, args.vm))}
    except spice.SpiceUnavailable as e:
        raise DaemonError("refused", str(e)) from None
    except libvirt.libvirtError as e:
        raise DaemonError("failed", describe_exception(e)) from e
