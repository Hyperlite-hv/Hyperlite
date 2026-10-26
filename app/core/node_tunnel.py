"""A read-only libvirt connection to a node registered over SSH only, for an API that does not hold the cluster's key
(docs/design/privilege-separation.md, section 13, the cluster domain, part 2).

hyperlite-daemon opens an SSH connection to the node with the cluster's key and forwards a local socket to the node's
read-only libvirt socket (`ssh -L <socket>:/var/run/libvirt/libvirt-sock-ro`); the API opens `qemu+unix` on that
socket. Whatever it asks, the node's libvirtd answers it as a read-only client: a change to that node goes through
the daemon's operations, which open a connection that may write (libvirt_utils.node_conn). One tunnel per node, kept
while its ssh runs, opened again when it ended."""

import logging
import subprocess
import threading
import time

from app.core import api_group, paths

logger = logging.getLogger(__name__)

DIR = paths.RUN / "nodes"
REMOTE_SOCKET = "/var/run/libvirt/libvirt-sock-ro"
OPEN_WAIT_S = 10

_lock = threading.Lock()
_tunnels = {}  # node name -> its ssh process (hyperlite-daemon's)


def socket_path(name):
    from app.core.cluster import NODE_NAME_RE

    if not NODE_NAME_RE.fullmatch(name):
        raise ValueError("Invalid node name")
    return DIR / f"{name}.sock"


def uri(name):
    """The API's read-only connection to the node `name`, once its tunnel is open."""
    return f"qemu+unix:///system?socket={socket_path(name)}"


def ensure(node):
    """The tunnel to the registered node `node` (its row of the node registry), opened when it is not: its socket's
    path. Raises OSError when ssh could not open it."""
    from app.core import cluster

    cluster.check_address(node["hostname"], node["ssh_user"])
    path = socket_path(node["name"])
    with _lock:
        running = _tunnels.get(node["name"])
        if running is not None and running.poll() is None and path.exists():
            return path
        if running is not None and running.poll() is None:
            running.terminate()
        DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
        # setgid, the API's group: every socket ssh makes there is that group's, for the API's read-only libvirt.
        api_group.share(DIR, 0o2750)
        path.unlink(missing_ok=True)
        cluster.ensure_known_host(node)
        process = subprocess.Popen(
            [
                "ssh",
                *cluster.node_ssh_options(
                    [
                        "-o",
                        "ConnectTimeout=8",
                        "-o",
                        "ExitOnForwardFailure=yes",
                        "-o",
                        "ServerAliveInterval=15",
                        "-o",
                        "StreamLocalBindUnlink=yes",
                        # The socket is root's and its group's (0660): step 4 gives that group to the API's user.
                        "-o",
                        "StreamLocalBindMask=0117",
                    ]
                ),
                "-N",
                "-L",
                f"{path}:{REMOTE_SOCKET}",
                "-p",
                str(node["ssh_port"]),
                "--",
                f"{node['ssh_user']}@{node['hostname']}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + OPEN_WAIT_S
        while not path.exists():
            if process.poll() is not None:
                raise OSError(f"ssh to {node['name']} ended: {(process.stderr.read() or '').strip()[-300:]}")
            if time.monotonic() > deadline:
                process.terminate()
                raise OSError(f"ssh to {node['name']} opened no socket within {OPEN_WAIT_S} s")
            time.sleep(0.1)
        _tunnels[node["name"]] = process
        logger.info("Tunnel to %s's read-only libvirt opened at %s", node["name"], path)
        return path


def close(name):
    """A node removed or renamed: its tunnel ends."""
    with _lock:
        process = _tunnels.pop(name, None)
    if process is not None and process.poll() is None:
        process.terminate()
    try:
        socket_path(name).unlink(missing_ok=True)
    except (ValueError, OSError) as e:
        logger.debug("Socket of %s not removed: %s", name, e)
