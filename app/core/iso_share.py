"""ISO images across the cluster: list every node's library and copy an image from one node to others.

Each node keeps its own library in the same absolute directory (Hyperlite is installed at the same path on every
node, the assumption migration already relies on, see app/routers/vms/migration.py), and a VM can only boot from
an image stored on its own host. Sharing therefore means copying: once copied, the image is an ordinary entry of
the target node's library, usable by that node for new VMs.

Transfers go through the local host with the cluster SSH key (the same trust as qemu+ssh:// connections); nodes
never talk to each other directly, since no node-to-node trust exists. A copy lands under a temporary name and is
renamed only once complete, so a half-copied image never appears in a library or a VM creation form.
"""

import logging
import os
import re
import shlex
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from app.core import cluster, node_health
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.safe_paths import safe_child
from app.core.tasks import create_task, finish_task, update_task_progress

logger = logging.getLogger(__name__)

LOCAL = "local"
PART_SUFFIX = ".part"
# Generous: a DVD image over a relayed Tailscale link can take the better part of an hour.
TRANSFER_TIMEOUT_S = 6 * 3600
LIST_TIMEOUT_S = 15
PROGRESS_EVERY_S = 3

# The same shape as an uploaded ISO name (see app/routers/isos.py): no path separator, no shell metacharacter.
_ISO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,250}\.iso$", re.IGNORECASE)

_active = set()
_active_lock = threading.Lock()


def isos_dir():
    from app.core import disk_library

    return disk_library.ISOS


def valid_iso_name(name):
    return bool(name) and Path(name).name == name and _ISO_NAME.match(name) is not None


def _iso_path(name):
    """The path of an image in the library, for a name checked here and not only by the callers: a path built from
    a name such as "../x" would leave the library, here and over SSH on the other nodes."""
    if not valid_iso_name(name):
        raise ValueError(f"Invalid ISO name '{name}'")
    return safe_child(isos_dir(), name)


# Where a node keeps its ISO library: from 1.1 where Proxmox VE keeps its own (app/core/paths.py), up to 1.0 under
# /root/hyperlite. During a rolling update the nodes do not all run the same version, so another node's library is
# looked for in both places.
LIBRARY_DIRS = ("/var/lib/hyperlite/template/iso", "/root/hyperlite/data/isos")
_PROBE = 'for d in "$@"; do if [ -d "$d" ]; then echo "$d"; exit 0; fi; done; exit 1'


def _library(node):
    """The ISO directory of `node` (None: this host). A node that has neither yet gets this host's convention."""
    if node is None:
        return isos_dir()
    r = _ssh(node, ["sh", "-c", _PROBE, "sh", *LIBRARY_DIRS], LIST_TIMEOUT_S)
    found = r.stdout.strip()
    return Path(found) if r.returncode == 0 and found in LIBRARY_DIRS else isos_dir()


def _remote_path(node, name):
    """The path of an image in another node's library, for a name checked here."""
    if not valid_iso_name(name):
        raise ValueError(f"Invalid ISO name '{name}'")
    return safe_child(_library(node), name)


def _target(node):
    return f"{node['ssh_user']}@{node['hostname']}"


def _ssh(node, command, timeout):
    """Run one shell command on a node. The command is a list, quoted here: ssh hands the remote shell a single
    string, so an unquoted '*.iso' would be expanded there."""
    return subprocess.run(
        [
            "ssh",
            *cluster.node_ssh_options(["-o", f"ConnectTimeout={node_health.CONNECT_TIMEOUT_S}"]),
            "-p",
            str(node["ssh_port"]),
            _target(node),
            shlex.join(command),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _scp(args, timeout=TRANSFER_TIMEOUT_S):
    r = subprocess.run(["scp", *cluster.node_ssh_options(), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "scp failed")


def _entry(name, size, mtime, node):
    return {
        "name": name,
        "size_mb": round(size / (1024 * 1024), 1),
        "added_at": datetime.fromtimestamp(mtime, UTC).isoformat(),
        "location": str(isos_dir()),
        "node": node,
    }


def local_isos():
    return [_entry(p.name, p.stat().st_size, p.stat().st_mtime, LOCAL) for p in sorted(isos_dir().glob("*.iso"))]


# What a library that could not be read says to the dashboard: fixed sentences, the node's own error (an SSH or shell
# message) stays in the service log.
NO_LIBRARY = "no Hyperlite ISO library on this node"
UNREADABLE = "the node's library could not be read (details in the service log)"
OFFLINE = "the node is offline"

# One connection per node lists its library: where it is (LIBRARY_DIRS, in order) and what it holds, in the same call.
# Exit status 3: neither directory exists.
_LIST = (
    'for d in "$@"; do if [ -d "$d" ]; then '
    'exec find "$d" -maxdepth 1 -type f -name "*.iso" -printf "%f\\t%s\\t%T@\\n"; fi; done; exit 3'
)


class NoLibrary(Exception):
    pass


def _remote_isos(node):
    r = _ssh(node, ["sh", "-c", _LIST, "sh", *LIBRARY_DIRS], LIST_TIMEOUT_S)
    if r.returncode == 3:
        raise NoLibrary()
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "unreachable")
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, size, mtime = parts
        try:
            out.append(_entry(name, int(size), float(mtime), node["name"]))
        except ValueError:
            continue
    return sorted(out, key=lambda e: e["name"])


def cluster_isos():
    """Every node's library. A node that cannot be read is reported next to the list instead of failing it; one the
    cluster sees down is reported at once, never waited for (app/core/node_health.py)."""
    nodes = cluster.list_nodes()
    isos = local_isos()
    seen_down = node_health.down(nodes)
    unreachable = [{"node": name, "error": OFFLINE} for name in sorted(seen_down)]
    nodes = [n for n in nodes if n["name"] not in seen_down]
    if nodes:
        with ThreadPoolExecutor(max_workers=min(8, len(nodes))) as pool:
            futures = {n["name"]: pool.submit(remote_isos, n) for n in nodes}
        for name, fut in futures.items():
            try:
                isos.extend(fut.result())
            except NoLibrary:
                unreachable.append({"node": name, "error": NO_LIBRARY})
            except Exception as e:  # one node's failure must not hide the others
                logger.warning("ISO library of %s not read: %s", name, e)
                unreachable.append({"node": name, "error": UNREADABLE})
    return {"isos": isos, "unreachable": unreachable}


def resolve_node(node_id):
    """None for the local host, the registered node otherwise. Raises LookupError for an unknown node."""
    if node_id in (None, "", LOCAL):
        return None
    node = cluster.get_node(node_id)
    if not node:
        raise LookupError(f"Node '{node_id}' not found")
    return node


def iso_size(node, name):
    """Size in bytes of an image in a node's library, None if it is not there. Another node's is read by
    hyperlite-daemon, over SSH with the cluster's key (iso.size)."""
    if not valid_iso_name(name):
        return None
    if node is None:
        p = _iso_path(name)
        return p.stat().st_size if p.is_file() else None
    return _daemon("iso.size", node=node["name"], name=name)["size"]


def remote_isos(node):
    """Another node's library, read by hyperlite-daemon (iso.list). NoLibrary when that node has none."""
    from app.daemon.registry import DaemonError

    try:
        return _daemon("iso.list", node=node["name"])["isos"]
    except DaemonError as e:
        if e.kind == "not_found" and e.detail == NO_LIBRARY:
            raise NoLibrary() from None
        raise RuntimeError(e.detail) from None


def delete_remote(node, name):
    """An image of another node's library deleted there by hyperlite-daemon (iso.delete_remote)."""
    from app.daemon.registry import DaemonError

    if not valid_iso_name(name):
        raise ValueError(f"Invalid ISO name '{name}'")
    try:
        _daemon("iso.delete_remote", node=node["name"], name=name)
    except DaemonError as e:
        raise RuntimeError(e.detail) from None


def _daemon(op, **args):
    from app.core import privileged

    return privileged.run(op, **args)


def _remote_size(node, name):
    if not valid_iso_name(name):
        return None
    r = _ssh(node, ["stat", "-c", "%s", str(_remote_path(node, name))], LIST_TIMEOUT_S)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _watch_progress(task_id, node, part_path, total, done):
    while not done.wait(PROGRESS_EVERY_S):
        try:
            if node is None:
                size = Path(part_path).stat().st_size if Path(part_path).exists() else 0
            else:
                r = _ssh(node, ["stat", "-c", "%s", str(part_path)], LIST_TIMEOUT_S)
                size = int(r.stdout.strip()) if r.returncode == 0 else 0
            update_task_progress(task_id, min(99, int(size * 100 / total)) if total else 0)
        except Exception as e:  # progress is cosmetic: a failed poll must not stop the copy
            logger.debug("ISO copy progress poll failed: %s", e)


def _copy(name, source, target, task_id, total):
    final = _iso_path(name)  # on this host
    source_file = final if source is None else _remote_path(source, name)
    target_dir = isos_dir() if target is None else _library(target)
    target_file = safe_child(target_dir, name)
    part = target_dir / f".{name}{PART_SUFFIX}"
    relay = None
    done = threading.Event()
    watcher = threading.Thread(target=_watch_progress, args=(task_id, target, part, total, done), daemon=True)
    watcher.start()
    try:
        if source is None:
            src = str(final)
        elif target is None:
            src = None
        else:
            # Remote to remote: relay through the local host, since the nodes do not trust each other.
            relay = isos_dir() / f".{name}.relay{PART_SUFFIX}"
            _scp(["-P", str(source["ssh_port"]), f"{_target(source)}:{source_file}", str(relay)])
            src = str(relay)

        if target is None:
            _scp(["-P", str(source["ssh_port"]), f"{_target(source)}:{source_file}", str(part)])
            os.replace(part, final)
        else:
            r = _ssh(target, ["mkdir", "-p", str(target_dir)], LIST_TIMEOUT_S)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:300] or "cannot prepare the ISO directory")
            _scp(["-P", str(target["ssh_port"]), src, f"{_target(target)}:{part}"])
            r = _ssh(target, ["mv", "-f", str(part), str(target_file)], LIST_TIMEOUT_S)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:300] or "cannot finish the copy")
    except BaseException:
        if target is None:
            part.unlink(missing_ok=True)
        else:
            try:
                _ssh(target, ["rm", "-f", str(part)], LIST_TIMEOUT_S)
            except Exception as e:  # a leftover hidden .part file is harmless, but say so
                logger.warning("Could not remove %s on %s: %s", part, target["name"], e)
        raise
    finally:
        done.set()
        if relay is not None:
            relay.unlink(missing_ok=True)


def start_copy(name, source_id, target_ids, username, after=None):
    """Validate, then start one background copy per target node. Returns the created task ids.

    after: called in the copy's thread once it ends, with True if it succeeded (see VM creation from an image
    stored on another node).

    Raises LookupError (unknown node or image), ValueError (bad request) or FileExistsError (the image is already
    on a target, or a copy of it to that target is already running)."""
    if not valid_iso_name(name):
        raise ValueError("Invalid ISO name")
    targets = list(dict.fromkeys(t or LOCAL for t in target_ids))
    if not targets:
        raise ValueError("Choose at least one target node")
    source_key = source_id or LOCAL
    if source_key in targets:
        raise ValueError("The source node cannot also be a target")
    source = resolve_node(source_key)
    resolved = [(t, resolve_node(t)) for t in targets]

    total = iso_size(source, name)
    if total is None:
        raise LookupError(f"ISO '{name}' not found on {source_key}")
    for key, node in resolved:
        if iso_size(node, name) is not None:
            raise FileExistsError(f"'{name}' is already on {key}")
    with _active_lock:
        busy = [key for key, _ in resolved if (name, key) in _active]
        if busy:
            raise FileExistsError(f"A copy of '{name}' to {busy[0]} is already running")
        _active.update((name, key) for key, _ in resolved)

    tasks = []
    for key, node in resolved:
        task_id = create_task("copy_iso", name, node=key, username=username)
        tasks.append({"node": key, "task_id": task_id})

        def job(key=key, node=node, task_id=task_id):
            ok = False
            try:
                # Copied by hyperlite-daemon, over SSH with the cluster's key; it writes the progress into the task.
                _daemon(
                    "iso.copy",
                    name=name,
                    source=None if source is None else source["name"],
                    target=None if node is None else node["name"],
                    task_id=task_id,
                    total=total,
                    timeout=TRANSFER_TIMEOUT_S + 60,
                )
                finish_task(task_id, "succeeded")
                log_action(username, "copy_iso", f"{name} {source_key} -> {key}", "success", task_id=task_id)
                ok = True
            except Exception as e:
                msg = describe_exception(e)
                finish_task(task_id, "failed", msg)
                log_action(username, "copy_iso", f"{name} {source_key} -> {key}", "failed", msg, task_id=task_id)
            finally:
                with _active_lock:
                    _active.discard((name, key))
            if after is not None:
                after(ok)

        threading.Thread(target=job, daemon=True).start()
    return tasks


def _delete_remote(node, name):
    r = _ssh(node, ["rm", "-f", "--", str(_remote_path(node, name))], LIST_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "deletion failed")
