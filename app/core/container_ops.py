"""What is done to an existing container of this node as root (app/routers/containers.py): started, stopped, its
settings changed, renamed, deleted. hyperlite-daemon runs it (container.*, app/daemon/ops/container.py,
docs/design/privilege-separation.md, section 13). The API keeps who may ask, the container's lock, its records
(app/core/container_meta.py) and its answers.

A container's filesystem is found in its libvirt definition (the filesystem mounted at /), never taken from the API's
records: it must be the directory named after the container in a place Hyperlite puts containers, the default one
or the hyperlite-containers directory of a local directory pool. A definition naming anything else (written by hand,
or by an API that should not be trusted with root's files) has its filesystem left alone: root deletes, renames and
writes into Hyperlite's own container directories only."""

import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import container_config, container_snapshots
from app.core.container_builder import CONTAINERS_DIR, set_container_hostname
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import pool_type_and_target_path
from app.core.network_alloc import release_static_ip
from app.core.safe_paths import safe_child
from app.core.vm_builder import validate_name
from app.core.vm_limits import validate_vm_resources

logger = logging.getLogger(__name__)

POOL_SUBDIR = "hyperlite-containers"


class ContainerError(Exception):
    """A refusal or a failure, with the daemon's kind."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def domain(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise ContainerError("not_found", f"Container '{name}' not found") from None


def homes(qemu):
    """The directories Hyperlite puts containers in: the default one, and the hyperlite-containers directory of every
    local directory pool (`qemu`: the node's own qemu connection, which holds the storage pools)."""
    found = [CONTAINERS_DIR]
    try:
        pools = qemu.listAllStoragePools(0)
    except libvirt.libvirtError as e:
        logger.warning("Storage pools not listed: only the default container directory is accepted (%s)", e)
        pools = []
    for pool in pools:
        kind, target = pool_type_and_target_path(pool)
        if kind == "dir" and target and target.startswith("/"):
            found.append(Path(target) / POOL_SUBDIR)
    return found


def rootfs(qemu, dom):
    """The container's filesystem, from its definition, when it is `<home>/<its name>` (homes()); refused otherwise."""
    root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    sources = [
        fs.find("source").get("dir")
        for fs in root.findall("./devices/filesystem")
        if fs.find("target") is not None and fs.find("target").get("dir") == "/" and fs.find("source") is not None
    ]
    if len(sources) == 1 and sources[0]:
        path = Path(os.path.normpath(sources[0]))
        if path.name == dom.name() and path.parent in homes(qemu):
            return path
    raise ContainerError("refused", "This container's filesystem is not in a container directory Hyperlite manages")


def start(conn, name):
    dom = domain(conn, name)
    # Answered before libvirt does: its own refusal surfaced as a 500, in the host's language.
    if dom.isActive():
        raise ContainerError("in_use", f"Container '{name}' is already running")
    try:
        dom.create()
    except libvirt.libvirtError as e:
        raise ContainerError("failed", describe_exception(e)) from e


def stop(conn, name, force=False, application=False, run_id=None):
    """A clean stop: SIGTERM to an application image's process, as `docker stop` does (libvirt's default looks for an
    init control pipe such an image does not have, then sends nothing at all), the init's shutdown for a system
    container; force: stopped at once. run_id: the run asked to stop (the domain's ID, its libvirt controller's pid);
    a new run started meanwhile is left alone. {"run_id", "stopped"}."""
    dom = domain(conn, name)
    if not dom.isActive():
        if run_id is not None:
            return {"run_id": None, "stopped": False}
        raise ContainerError("in_use", f"Container '{name}' is already stopped")
    current = dom.ID()
    if run_id is not None and current != run_id:
        return {"run_id": current, "stopped": False}
    try:
        if force:
            dom.destroy()
        elif application:
            dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_SIGNAL)
        else:
            dom.shutdown()
    except libvirt.libvirtError as e:
        raise ContainerError("failed", describe_exception(e)) from e
    return {"run_id": current, "stopped": True}


def update(qemu, conn, name, vcpu=None, memory_mb=None, autostart=None, dns=None):
    """{"changes": [what changed, said for the audit log], "needs_restart"}."""
    if vcpu is not None or memory_mb is not None:
        errors = validate_vm_resources(vcpu, memory_mb)
        if errors:
            raise ContainerError("refused", errors)
    dom = domain(conn, name)
    changes, restart = [], False
    try:
        if dns is not None:
            servers = container_config.write_dns(rootfs(qemu, dom), dns)
            changes.append(f"DNS {', '.join(servers)}")
        if vcpu is not None or memory_mb is not None:
            restart = container_config.set_resources(conn, dom, vcpu, memory_mb)["needs_restart"]
            dom = conn.lookupByName(name)  # the definition was replaced
            changes.append(f"{vcpu or '-'} vCPU, {memory_mb or '-'} MB")
        if autostart is not None:
            dom.setAutostart(1 if autostart else 0)
            changes.append(f"start at boot {'on' if autostart else 'off'}")
    except container_config.ConfigError as e:
        raise ContainerError("refused", str(e)) from e
    except libvirt.libvirtError as e:
        raise ContainerError("failed", describe_exception(e)) from e
    if not changes:
        raise ContainerError("refused", "No change requested")
    return {"changes": changes, "needs_restart": restart}


def _interface(dom):
    """(MAC address, network) of the container's first interface."""
    iface = ET.fromstring(dom.XMLDesc(0)).find(".//devices/interface")
    if iface is None:
        return None, None
    mac, source = iface.find("mac"), iface.find("source")
    return (mac.get("address") if mac is not None else None), (source.get("network") if source is not None else None)


def _filesystem_source(dom):
    for fs in ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).findall("./devices/filesystem"):
        if fs.find("source") is not None and fs.find("source").get("dir"):
            return fs.find("source").get("dir")
    return None


def delete(qemu, conn, name, application=False):
    """Stopped at once, undefined, its snapshots and its filesystem removed; an application container's fixed address
    released. A filesystem outside Hyperlite's container directories stays (rootfs()): {"filesystem_kept": path}."""
    dom = domain(conn, name)
    try:
        path, kept = rootfs(qemu, dom), None
    except ContainerError:
        path, kept = None, _filesystem_source(dom)
    mac, network = _interface(dom)
    try:
        if dom.isActive():
            dom.destroy()
        dom.undefine()
    except libvirt.libvirtError as e:
        raise ContainerError("failed", describe_exception(e)) from e
    if path is not None:
        container_snapshots.delete_all(name, path.parent)
        shutil.rmtree(path, ignore_errors=True)
    else:
        logger.warning("Filesystem %s of the deleted container %s left in place: not Hyperlite's", kept, name)
    if application and mac and network:
        try:
            release_static_ip(qemu, network, mac)
        except libvirt.libvirtError:
            logger.warning("Reservation of %s on %s left after the container %s", mac, network, name, exc_info=True)
    return {"filesystem_kept": kept}


def rename(qemu, conn, name, new):
    """libvirt's LXC driver cannot rename a domain: the stopped container is redefined under its new name, its
    filesystem directory renamed next to the old one (its snapshots' too), its host name changed inside. Each step is
    undone when a later one fails."""
    error = validate_name(new, "container")
    if error:
        raise ContainerError("refused", error)
    if new == name:
        raise ContainerError("refused", "The new name is the current one")
    dom = domain(conn, name)
    try:
        conn.lookupByName(new)
    except libvirt.libvirtError:
        pass
    else:
        raise ContainerError("in_use", f"A container named '{new}' already exists")
    if dom.isActive():
        raise ContainerError("in_use", "Stop the container before renaming it")
    old_root = rootfs(qemu, dom)
    new_root = safe_child(old_root.parent, new)
    if os.path.lexists(new_root):
        raise ContainerError("in_use", f"A filesystem named '{new}' is already on disk: {new_root}")
    old_xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    autostart = bool(dom.autostart())
    root = ET.fromstring(old_xml)
    root.find("name").text = new
    for source in root.findall("./devices/filesystem/source"):
        if source.get("dir") and Path(os.path.normpath(source.get("dir"))) == old_root:
            source.set("dir", str(new_root))
    try:
        container_snapshots.rename_all(name, new, old_root.parent)
    except (OSError, container_snapshots.SnapshotError) as e:
        raise ContainerError("in_use", f"Could not rename the container's snapshots: {e}") from e
    try:
        os.rename(old_root, new_root)
    except OSError as e:
        container_snapshots.rename_all(new, name, old_root.parent)
        raise ContainerError("failed", f"Could not rename the filesystem: {e}") from e
    try:
        dom.undefine()
        try:
            made = conn.defineXML(ET.tostring(root, encoding="unicode"))
        except libvirt.libvirtError:
            conn.defineXML(old_xml)
            raise
    except libvirt.libvirtError as e:
        os.rename(new_root, old_root)
        container_snapshots.rename_all(new, name, old_root.parent)
        raise ContainerError("failed", f"Rename failed: {describe_exception(e)}") from e
    if autostart:
        made.setAutostart(1)
    try:
        set_container_hostname(new_root, new)
    except OSError as e:
        logger.warning("Host name of the renamed container %s not updated: %s", new, e)


# --- Part 3: snapshots and the console log, read and written in the container's own directory ---


def _stopped(conn, qemu, name, action):
    """(domain, filesystem) of a stopped container: its files are copied or swapped."""
    dom = domain(conn, name)
    if dom.isActive():
        raise ContainerError("in_use", f"Stop the container before {action}")
    return dom, rootfs(qemu, dom)


def snapshots(qemu, conn, name):
    return container_snapshots.list_snapshots(rootfs(qemu, domain(conn, name)))


def snapshot_create(qemu, conn, name, snapshot, description=None, check_only=False):
    error = validate_name(snapshot, "snapshot")
    if error:
        raise ContainerError("refused", error)
    _dom, path = _stopped(conn, qemu, name, "taking a snapshot (its files are copied)")
    if container_snapshots.exists(path, snapshot):
        raise ContainerError("in_use", f"A snapshot '{snapshot}' already exists for this container")
    if check_only:
        return None
    try:
        return container_snapshots.create(path, snapshot, description)
    except container_snapshots.SnapshotError as e:
        raise ContainerError("failed", str(e)[:500]) from e


def _existing(qemu, conn, name, snapshot):
    path = rootfs(qemu, domain(conn, name))
    if validate_name(snapshot, "snapshot") or not container_snapshots.exists(path, snapshot):
        raise ContainerError("not_found", f"Snapshot '{snapshot}' not found")
    return path


def snapshot_restore(qemu, conn, name, snapshot, check_only=False):
    _existing(qemu, conn, name, snapshot)
    _dom, path = _stopped(conn, qemu, name, "restoring a snapshot")
    if check_only:
        return
    try:
        container_snapshots.restore(path, snapshot)
    except container_snapshots.SnapshotError as e:
        raise ContainerError("failed", str(e)[:500]) from e


def snapshot_delete(qemu, conn, name, snapshot):
    path = _existing(qemu, conn, name, snapshot)
    try:
        container_snapshots.delete(path, snapshot)
    except (OSError, container_snapshots.SnapshotError) as e:
        raise ContainerError("failed", str(e)) from e


def log(qemu, conn, name, lines):
    """The last lines an application container's process printed (what `docker logs` shows): {"active", "lines"},
    lines None when it has no log. The log is trimmed as it is read."""
    from app.core import container_console

    dom = domain(conn, name)
    return {"active": bool(dom.isActive()), "lines": container_console.read_tail(rootfs(qemu, dom), lines)}


# --- The containers' archives (docs/design/privilege-separation.md, the backups domain, part 4) ---

# What an archive is named (`<container>-<time>.tar.gz`, archive): the only files archive_delete removes, and the only
# ones archive_restore unpacks.
ARCHIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}-\d{8}T\d{6}Z\.tar\.gz")


def archive_path(path):
    """An archive of the containers' backup directory, whatever the records say (the API writes them)."""
    from app.core import paths

    p = Path(str(path or ""))
    if p.parent != paths.CONTAINER_BACKUPS or not ARCHIVE_NAME.fullmatch(p.name):
        raise ContainerError("refused", f"Not a container archive: {path}")
    return p


def archive(qemu, conn, name):
    """A stopped container's filesystem, the one its definition names, archived into the containers' backup
    directory: {"path", "size"}."""
    import subprocess
    from datetime import UTC, datetime

    from app.core import paths

    _dom, src = _stopped(conn, qemu, name, "backing it up (no hot backup for containers)")
    paths.CONTAINER_BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = paths.CONTAINER_BACKUPS / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    try:
        subprocess.run(
            ["tar", "-czf", str(dest), "-C", str(src.parent), src.name], check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        dest.unlink(missing_ok=True)
        raise ContainerError("failed", f"The archive failed: {(e.stderr or '').strip()[:300]}") from None
    return {"path": str(dest), "size": dest.stat().st_size}


def archive_delete(path):
    """An archive removed: False when there is none at that path."""
    p = archive_path(path)
    if not p.is_file() or p.is_symlink():
        return False
    p.unlink()
    return True


def archive_restore(conn, path, name, original):
    """An archive unpacked as the container `name` in the default containers' directory, and defined: as an
    application container when `original` was one (its image's process, its network), else as a system container.
    {"mac", "ip", "network"}, for the API's records."""
    import subprocess

    import libvirt as _libvirt

    from app.core import container_builder, container_create
    from app.core.container_meta import get_container_app
    from app.core.error_messages import describe_exception
    from app.core.network_alloc import generate_mac, release_static_ip

    tar = archive_path(path)
    try:
        conn.lookupByName(name)
        raise ContainerError("in_use", f"A container '{name}' already exists: delete it first or choose another name")
    except _libvirt.libvirtError:
        pass
    try:
        root = container_builder.restore_container_rootfs(tar, name, original_name=original)
    except (subprocess.CalledProcessError, ValueError) as e:
        msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
        raise ContainerError("failed", f"Restore failed: {msg}") from None
    # KNOWN LIMITATION: the original vcpu/memory are not kept in the archive (unlike cloning, which reads them
    # straight from the still defined source domain): the default values, adjustable afterwards.
    app = get_container_app(original)
    network = (app["network"] or "default") if app else "default"
    mac = ip = None
    try:
        if app:
            xml, mac, ip = container_create.app_definition(conn, name, root, app["spec"], network, 1, 512)
        else:
            mac = generate_mac(conn)
            xml = container_builder.build_container_xml(name, 1, 512, root, network=network, mac=mac)
        conn.defineXML(xml)
    except (ValueError, _libvirt.libvirtError) as e:
        if app and ip:
            release_static_ip(conn, network, mac)
        container_builder.delete_container_rootfs(name)
        detail = describe_exception(e) if isinstance(e, _libvirt.libvirtError) else str(e)
        raise ContainerError("failed", f"Failed to define the restored container: {detail}") from None
    return {"mac": mac, "ip": ip, "network": network}
