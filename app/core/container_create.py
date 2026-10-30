"""A container made on this node (POST /containers) or cloned (POST /containers/{name}/clone): its filesystem, from
the local Debian base or an image pulled from a registry, configured for a system container (systemd, sshd, the
account) or prepared for an application container (the image's own process, a fixed address), then its definition.
hyperlite-daemon runs it (container.create, container.clone, app/daemon/ops/container.py,
docs/design/privilege-separation.md, section 13): the filesystem is root's, written in a directory Hyperlite puts
containers in (app/core/container_ops.py), chosen here from the pool's own definition. The API keeps who may ask, the
lock, the task and the container's records (app/core/container_meta.py)."""

import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Literal

import libvirt
from pydantic import BaseModel, Field

from app.core import container_console, container_ops
from app.core.container_builder import (
    CONTAINERS_DIR,
    app_spec,
    build_container_xml,
    clone_container_rootfs,
    configure_container_rootfs,
    create_container_rootfs,
    prepare_app_rootfs,
    resolve_init,
    validate_app_overrides,
)
from app.core.error_messages import describe_exception
from app.core.network_alloc import allocate_static_ip, generate_mac, network_gateway, release_static_ip
from app.core.safe_paths import safe_child
from app.core.vm_builder import get_or_create_automation_pubkey, validate_name, validate_username
from app.core.vm_limits import compute_limits

logger = logging.getLogger(__name__)

ContainerError = container_ops.ContainerError


class ContainerCreate(BaseModel):
    name: str
    vcpu: int = Field(default=1, ge=1)
    memory_mb: int = Field(default=512, ge=128)
    # Required for a system container (the account created in it); unused by an application container.
    username: str | None = None
    password: str | None = None
    network: str = "default"
    # None/empty = the local Debian 12 base (fast, already cached); otherwise a
    # Docker Hub image reference (or any OCI registry), e.g. "ubuntu:22.04",
    # "alpine:3.19", "nginx:latest". See container_builder.pull_image_rootfs.
    image: str | None = None
    # "application": run the image's own process (the default with an image); "system": boot it as a small
    # system with systemd and sshd (the only choice without an image). See container_builder.read_image_config.
    mode: Literal["application", "system"] | None = None
    # A "dir" storage pool to hold the container's filesystem (None: the default /var/lib/libvirt/containers).
    storage_pool: str | None = None
    # Application containers only: replaces the image's Entrypoint+Cmd, and adds to or overrides its environment.
    command: list[str] | None = None
    env: dict[str, str] | None = None


class AppSpec(BaseModel):
    """An application container's process, as recorded at its creation (container_builder.app_spec): a clone runs the
    same."""

    args: list[str] = Field(min_length=1, max_length=64)
    env: dict[str, str] = Field(default_factory=dict, max_length=256)
    cwd: str = Field(default="/", max_length=4096)
    uid: int = Field(default=0, ge=0, le=2**31)
    gid: int = Field(default=0, ge=0, le=2**31)


def home_for(qemu, pool_name):
    """(directory, pool) a new container's filesystem goes in: the hyperlite-containers directory of a local directory
    pool, or the default one. NFS, ZFS and iSCSI pools are refused: a container filesystem needs root ownership, device
    files and extended attributes, which an NFS export (root squash) does not reliably keep, and ZFS/iSCSI pools hold
    block volumes, not directories."""
    if not pool_name:
        return CONTAINERS_DIR, None
    from app.core.libvirt_utils import lookup_pool, pool_type_and_target_path

    try:
        pool = lookup_pool(qemu, pool_name)
    except libvirt.libvirtError:
        raise ContainerError("refused", f"Storage pool '{pool_name}' not found") from None
    kind, path = pool_type_and_target_path(pool)
    if kind != "dir":
        raise ContainerError(
            "refused", f"Storage pool '{pool_name}' is not a local directory pool: containers need one"
        )
    if not pool.isActive():
        raise ContainerError("refused", f"Storage pool '{pool_name}' is not started")
    if not path or not path.startswith("/"):
        raise ContainerError("refused", f"Storage pool '{pool_name}' has no usable path")
    return safe_child(path, container_ops.POOL_SUBDIR), pool_name


def _limits(spec):
    """The node's limits, said in one sentence as the route always did."""
    limits, errors = compute_limits(), []

    def origin(lim):
        return f"variable {lim['variable']}" if lim["source"] == "configuration" else "technical ceiling"

    if spec.vcpu > limits["vcpu"]["max"]:
        errors.append(f"vCPU: {spec.vcpu} is above the limit ({limits['vcpu']['max']}, {origin(limits['vcpu'])})")
    if spec.memory_mb > limits["memory_mb"]["max"]:
        errors.append(
            f"Memory: {spec.memory_mb} MB is above the limit "
            f"({limits['memory_mb']['max']} MB, {origin(limits['memory_mb'])})"
        )
    if errors:
        raise ContainerError("refused", " ; ".join(errors))


def check(qemu, lxc, spec, mode):
    """Every refusal before the image is fetched (a download of hundreds of MB must not end in one), all at once:
    (home, pool) where its filesystem goes."""
    _limits(spec)
    errors = []
    name_error = validate_name(spec.name, "container")
    if name_error:
        errors.append(name_error)
    if mode == "application":
        if not spec.image:
            errors.append("An application container needs an image")
        errors.extend(validate_app_overrides(spec.command, spec.env))
    else:
        if spec.command or spec.env:
            errors.append("A command or environment variables apply to application containers only")
        username_error = validate_username(spec.username or "")
        if username_error:
            errors.append(username_error)
        if len(spec.password or "") < 4:
            errors.append("The password must contain at least 4 characters")
    try:
        lxc.lookupByName(spec.name)
        errors.append(f"A container named '{spec.name}' already exists")
    except libvirt.libvirtError:
        pass
    home = pool = None
    try:
        home, pool = home_for(qemu, spec.storage_pool)
    except ContainerError as e:
        errors.append(e.detail)
    try:
        if not qemu.networkLookupByName(spec.network).isActive():
            errors.append(f"Network '{spec.network}' is not started")
    except libvirt.libvirtError:
        logger.debug("Network %s not found while a container was checked", spec.network)
    if mode == "application":
        try:
            if network_gateway(qemu, spec.network) is None:
                errors.append(
                    f"Network '{spec.network}' has no Hyperlite-managed subnet: an application container needs a NAT "
                    "or isolated network"
                )
        except libvirt.libvirtError:
            errors.append(f"Network '{spec.network}' not found")
    if errors:
        raise ContainerError("refused", errors)
    return home, pool


def app_definition(qemu, name, rootfs, spec, network, vcpu, memory_mb):
    """Reserve an address on the network, prepare the image's filesystem and build the definition of an application
    container: (xml, mac, ip). The reservation is released again when anything after it fails."""
    gateway = network_gateway(qemu, network)
    if gateway is None:
        raise ValueError(
            f"Network '{network}' has no Hyperlite-managed subnet: an application container needs a NAT or isolated "
            "network, whose address it receives from Hyperlite"
        )
    mac = generate_mac(qemu)
    ip = allocate_static_ip(qemu, network, mac)
    if not ip:
        raise ValueError(f"Network '{network}' has no free address left in its DHCP range")
    try:
        init = resolve_init(rootfs, spec)
        prepare_app_rootfs(rootfs, name, gateway[0])
        app = {"init": init, "spec": spec, "ip": ip, "prefix": gateway[1], "gateway": gateway[0]}
        if container_console.install(rootfs, int(spec.get("uid", 0)), int(spec.get("gid", 0))):
            app["launcher"] = (container_console.LAUNCHER, container_console.LOG)
        return build_container_xml(name, vcpu, memory_mb, rootfs, network=network, mac=mac, app=app), mac, ip
    except Exception:
        release_static_ip(qemu, network, mac)
        raise


def create(qemu, lxc, spec, check_only=False):
    """The container made and started. {"pool", "base_dir", "mode", "app": {"spec", "ip"} or None, "start_error"}:
    an application container whose process does not start stays defined, so it can be fixed or deleted. None with
    check_only."""
    mode = spec.mode or ("application" if spec.image else "system")
    home, pool = check(qemu, lxc, spec, mode)
    if check_only:
        return None
    rootfs = None
    mac = None
    try:
        try:
            rootfs, family = create_container_rootfs(spec.name, image=spec.image, bootstrap=mode == "system", home=home)
            if mode == "system":
                configure_container_rootfs(
                    rootfs, spec.name, spec.username, spec.password, get_or_create_automation_pubkey(), family=family
                )
        except subprocess.CalledProcessError as e:
            what = "fetch the image" if mode == "application" else "prepare the filesystem"
            raise ContainerError("failed", f"Failed to {what}: {e.stderr or e}") from e
        except ValueError as e:
            raise ContainerError("refused", str(e)) from e
        app = None
        if mode == "application":
            try:
                process = app_spec(spec.image, spec.command, spec.env)
                xml, mac, ip = app_definition(qemu, spec.name, rootfs, process, spec.network, spec.vcpu, spec.memory_mb)
                domain = lxc.defineXML(xml)
            except ValueError as e:
                raise ContainerError("refused", str(e)) from e
            app = {"spec": process, "ip": ip}
        else:
            mac = generate_mac(qemu)
            xml = build_container_xml(spec.name, spec.vcpu, spec.memory_mb, rootfs, network=spec.network, mac=mac)
            domain = lxc.defineXML(xml)
    except BaseException as e:
        if mode == "application" and mac:  # reserved, then the definition was refused
            release_static_ip(qemu, spec.network, mac)
        if rootfs is not None:
            shutil.rmtree(rootfs, ignore_errors=True)
        if isinstance(e, libvirt.libvirtError):
            raise ContainerError("failed", describe_exception(e)) from e
        raise
    result = {"pool": pool, "base_dir": str(home) if pool else None, "mode": mode, "app": app, "start_error": None}
    try:
        domain.create()
    except libvirt.libvirtError as e:
        if mode != "application":
            domain.undefine()
            shutil.rmtree(rootfs, ignore_errors=True)
            raise ContainerError("failed", f"Failed to start the container: {describe_exception(e)}") from e
        # Defined but not started (a wrong command, for instance): kept, so it can be fixed or deleted.
        result["start_error"] = describe_exception(e)
    return result


def clone(qemu, lxc, name, new_name, app=None):
    """The stopped container copied as `new_name`, in its source's directory, with a new MAC address (and, for an
    application container, a new fixed address and the same process, `app`). {"ip"}."""
    source = container_ops.domain(lxc, name)
    error = validate_name(new_name, "container")
    if error:
        raise ContainerError("refused", error)
    try:
        lxc.lookupByName(new_name)
    except libvirt.libvirtError:
        pass
    else:
        raise ContainerError("in_use", f"A container '{new_name}' already exists")
    if source.isActive():
        raise ContainerError("in_use", "Stop the container before cloning it")
    src = container_ops.rootfs(qemu, source)
    root = ET.fromstring(source.XMLDesc(0))
    vcpu = int(root.findtext("vcpu") or "1")
    memory_mb = int(root.findtext("memory") or str(512 * 1024)) // 1024
    iface = root.find("./devices/interface/source")
    network = iface.get("network") if iface is not None else None
    try:
        rootfs = clone_container_rootfs(src, safe_child(src.parent, new_name))
    except (subprocess.CalledProcessError, ValueError) as e:
        msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
        raise ContainerError("failed", f"Failed to copy the filesystem: {msg}") from e
    mac = ip = None
    try:
        if app:
            xml, mac, ip = app_definition(qemu, new_name, rootfs, app, network, vcpu, memory_mb)
        else:
            mac = generate_mac(qemu)
            xml = build_container_xml(new_name, vcpu, memory_mb, rootfs, network=network, mac=mac)
        lxc.defineXML(xml)
    except (ValueError, libvirt.libvirtError) as e:
        msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
        if ip:
            release_static_ip(qemu, network, mac)
        shutil.rmtree(rootfs, ignore_errors=True)
        raise ContainerError("failed", f"Failed to define the cloned container: {msg}") from e
    return {"ip": ip, "network": network}
