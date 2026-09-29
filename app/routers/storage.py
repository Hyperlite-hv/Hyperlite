import contextlib
import logging
import re
import shutil
import socket
import xml.etree.ElementTree as ET
from typing import Literal
from xml.sax import saxutils

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import iscsi, nfs_permissions, zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import ensure_default_pool, get_disk_paths_in_use, lookup_volume, open_conn
from app.core.security import get_current_user, require_role
from app.core.vm_builder import validate_name
from app.core.vm_limits import validate_vm_resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])

# This table did not match libvirt's real enumeration (virStoragePoolState,
# checked through libvirt.VIR_STORAGE_POOL_*: only 5 values, 0-4, not 6). Every
# pool, including an already active "default", was displayed as
# "en_construction". It had no visible effect while no screen displayed this
# "state" field.
POOL_STATE_NAMES = {
    0: "inactif",  # VIR_STORAGE_POOL_INACTIVE
    1: "en_construction",  # VIR_STORAGE_POOL_BUILDING
    2: "actif",  # VIR_STORAGE_POOL_RUNNING
    3: "degrade",  # VIR_STORAGE_POOL_DEGRADED
    4: "inaccessible",  # VIR_STORAGE_POOL_INACCESSIBLE
}

# Host name/IP (NFS pool): letters/digits/dots/dashes. Enough for a hostname or
# a simple IPv4/IPv6 address, and it excludes any character that could have a
# special meaning elsewhere.
NFS_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.:_-]{0,253})$")
# Absolute path (server-side NFS export, or the local directory of a "dir"
# pool): no spaces or special XML/shell characters.
POOL_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,255}$")


def _pool_type(pool):
    try:
        root = ET.fromstring(pool.XMLDesc(0))
        return root.get("type", "inconnu")
    except (libvirt.libvirtError, ET.ParseError):
        return "inconnu"


def _pool_path(pool):
    try:
        return ET.fromstring(pool.XMLDesc(0)).findtext("target/path")
    except (libvirt.libvirtError, ET.ParseError):
        return None


_FS_NS = "{http://libvirt.org/schemas/storagepool/fs/1.0}"


def _nfs_version(pool):
    """The 'vers=' mount option of an NFS pool (None for one defined outside Hyperlite without it)."""
    try:
        root = ET.fromstring(pool.XMLDesc(0))
    except (libvirt.libvirtError, ET.ParseError):
        return None
    for option in root.iter(f"{_FS_NS}option"):
        name = option.get("name", "")
        if name.startswith("vers="):
            return name.split("=", 1)[1]
    return None


def _pool_summary(pool):
    state, capacity, allocation, available = pool.info()
    if _pool_type(pool) == "iscsi":
        # libvirt counts every LUN as fully allocated, so an iSCSI pool always looked 100 % full. What matters is
        # how much of it VMs already use: a LUN is taken whole or not at all.
        try:
            # LUNs appear and disappear on the storage server: the cached list would count stale ones.
            if pool.isActive():
                pool.refresh(0)
            in_use = get_disk_paths_in_use(pool.connect())
            allocation = sum(v.info()[1] for v in pool.listAllVolumes() if v.path() in in_use)
            available = capacity - allocation
        except libvirt.libvirtError:
            pass
    return {
        "nfs_version": _nfs_version(pool) if _pool_type(pool) == "netfs" else None,
        "chemin": _pool_path(pool),
        "nom": pool.name(),
        "uuid": pool.UUIDString(),
        "type": _pool_type(pool),
        "etat": POOL_STATE_NAMES.get(state, "inconnu"),
        "autostart": bool(pool.autostart()),
        "capacite_go": round(capacity / (1024**3), 2),
        "allocation_go": round(allocation / (1024**3), 2),
        "disponible_go": round(available / (1024**3), 2),
    }


@router.get("")
def list_pools(node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the same convention as GET /vms: list the pools of a registered remote
    node instead of the local host.

    ZFS pools are merged into the same list (type='zfs') for a unified display
    in the UI, but only when `node` designates the LOCAL host: unlike libvirt
    pools (dir/netfs), ZFS management is still single-node (direct `zpool`/`zfs`
    calls on THIS server, no remote management over SSH; see
    app/core/zfs_storage.py). A ZFS pool of a registered remote node is
    therefore not visible here for now."""
    conn = open_conn(node)
    try:
        ensure_default_pool(conn)
        result = []
        for pool in conn.listAllStoragePools():
            # A pool removed while the list is built (another session deleting it) is skipped, not a failed list.
            try:
                result.append(_pool_summary(pool))
            except libvirt.libvirtError as e:
                if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_POOL:
                    raise
                logger.debug("Storage pool vanished while listing: %s", e)
        if not node or node == "local":
            result += zfs_storage.list_pools()
        for p in result:
            p["noeud"] = node or "local"
            p.setdefault("chemin", None)
        log_action(user["username"], "list_storage_pools", "storage", "succes")
        return result
    finally:
        conn.close()


class PoolCreate(BaseModel):
    name: str
    # "dir": a directory local to the node (like the existing "default" pool).
    # "netfs": a remote NFS export mounted by libvirt (shared storage), the
    # foundation of live migration: a disk on a netfs pool is visible identically from
    # any node that mounts the same export, so it does not need to be copied when a VM
    # is migrated.
    # "zfs": a ZFS pool managed outside libvirt (see app/core/zfs_storage.py), backed
    # for now by a loopback file (`size_gb`) rather than a dedicated disk, so the
    # mechanism can be validated without touching a node's existing LVM.
    # "iscsi": a target on a NAS or storage array (see app/core/iscsi.py); its LUNs, created on the storage side,
    # become VM disks.
    type: str = Field(pattern="^(dir|netfs|zfs|iscsi)$")
    path: str | None = None  # pool "dir" : repertoire local (defaut si omis)
    nfs_host: str | None = None  # "netfs" pool: NFS server host
    nfs_export_path: str | None = None  # pool "netfs" : chemin exporte cote serveur
    # "netfs" pool: the NFS protocol version to mount with. Always explicit, never negotiated (see _build_pool_xml):
    # 4.2 unless the server only offers an older one (a NAS limited to NFSv3, for instance).
    nfs_version: Literal["3", "4", "4.0", "4.1", "4.2"] = "4.2"
    size_gb: int | None = Field(None, ge=1)  # "zfs" pool: size of the loopback file
    iscsi_host: str | None = None  # "iscsi" pool: portal address of the storage server
    iscsi_port: int = Field(iscsi.DEFAULT_PORT, ge=1, le=65535)
    iscsi_target: str | None = None  # "iscsi" pool: target name (IQN)
    chap_user: str | None = None  # "iscsi" pool: CHAP credentials, when the target requires them
    chap_password: str | None = Field(None, max_length=255)


def _build_pool_xml(payload: PoolCreate, target_path: str) -> str:
    """Build the libvirt XML with ElementTree (automatic escaping) rather than by
    string concatenation: the security audit had found an XML injection in
    bridge-mode network creation for exactly that reason, and the mistake must
    not be repeated here."""
    pool_el = ET.Element("pool", type=payload.type)
    ET.SubElement(pool_el, "name").text = payload.name
    if payload.type == "iscsi":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=payload.iscsi_host, port=str(payload.iscsi_port))
        ET.SubElement(source_el, "device", path=payload.iscsi_target)
        if payload.chap_user:
            auth_el = ET.SubElement(source_el, "auth", type="chap", username=payload.chap_user)
            ET.SubElement(auth_el, "secret", usage=iscsi.secret_usage(payload.name))
        target_el = ET.SubElement(pool_el, "target")
        ET.SubElement(target_el, "path").text = iscsi.BY_PATH
        return ET.tostring(pool_el, encoding="unicode")
    if payload.type == "netfs":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=payload.nfs_host)
        ET.SubElement(source_el, "dir", path=payload.nfs_export_path)
        ET.SubElement(source_el, "format", type="nfs")
        # NFS mount options, found by testing a real NFS share between two machines:
        #
        # 1) On a recent Debian 13 NFS client (recent nfs-utils and kernel) the mount
        # always fails with "NFS: mount program didn't pass remote address". A manual
        # mount(8) WITHOUT an explicit 'addr=' option fails the same way, and it succeeds
        # WITH it. This looks like a regression of the recent mount path (the new
        # fsconfig kernel mount API), which no longer derives the address from the given
        # host name. It is added systematically, harmless on an older NFS client that does
        # not need it. 'addr' wants an IP, not a host name; gethostbyname() on a literal
        # IP returns it unchanged (a no-op), so the case does not need to be detected
        # beforehand.
        #
        # 2) Even with 'addr=' passed correctly, the mount then fails with "NFS: Version
        # unavailable" unless the NFS version is fixed explicitly: automatic negotiation
        # fails silently on this client. 'vers=' is therefore always given: 4.2 by default, or the version chosen
        # at creation for a server that does not offer it (NFSv3-only appliances are common).
        #
        # 3) The libvirt element is 'mount_opts' (NOT 'mountopts', which libvirt ignores
        # silently) and it lives in its OWN XML namespace (checked in
        # /usr/share/libvirt/schemas/storagepool.rng, not in the online documentation).
        # Built with ET.SubElement and a qualified '{namespace}mount_opts' tag,
        # ET.tostring() declares the namespace as a PREFIX on the root
        # (<pool xmlns:ns0="..."> then <ns0:mount_opts>). That is syntactically correct,
        # but libvirt on this version still ignores it silently (the element is absent
        # from the XML read back right after defineXML). Only the form with "xmlns=..."
        # on the element itself (a LOCAL default namespace, not a root prefix) is honoured,
        # and ElementTree never generates that exact form. The rest of the document is
        # still built with ElementTree (automatic escaping, see above); only this fragment
        # is assembled as a string, with values that are already validated (NFS_HOST_RE
        # above) or resolved through gethostbyname, never raw user text.
        try:
            addr = socket.gethostbyname(payload.nfs_host)
        except OSError:
            addr = payload.nfs_host  # resolution failed: try anyway with the value as provided
        mount_opts_xml = (
            f'<mount_opts xmlns="http://libvirt.org/schemas/storagepool/fs/1.0">'
            f'<option name="addr={saxutils.escape(addr)}"/><option name="vers={payload.nfs_version}"/></mount_opts>'
        )
    else:
        mount_opts_xml = ""
    target_el = ET.SubElement(pool_el, "target")
    ET.SubElement(target_el, "path").text = target_path
    pool_xml = ET.tostring(pool_el, encoding="unicode")
    if mount_opts_xml:
        pool_xml = pool_xml.replace("</source>", "</source>" + mount_opts_xml, 1)
    return pool_xml


@router.get("/support")
def storage_support(user: dict = Depends(get_current_user)):
    """Which pool types the local host can create now, and why not: read by the pool creation form to warn
    before the attempt. nfs: 'ok' or 'no_client'; zfs: see zfs_storage.status(). Nothing is loaded or run."""
    return {
        "nfs": "ok" if shutil.which("mount.nfs") else "no_client",
        "zfs": zfs_storage.status(),
        "iscsi": "ok" if iscsi.initiator_available() else "no_initiator",
        # The storage side must allow this name (ACL) before it shows any LUN.
        "iscsi_initiator": iscsi.initiator_name(),
    }


@router.post("", status_code=201)
def create_pool(payload: PoolCreate, node: str | None = None, user: dict = Depends(require_role("admin"))):
    name_error = validate_name(payload.name, "storage pool")
    if name_error:
        log_action(user["username"], "create_storage_pool", payload.name, "echec", name_error)
        raise HTTPException(status_code=422, detail=name_error)

    if payload.type == "zfs":
        if node and node != "local":
            raise HTTPException(
                status_code=422,
                detail="A ZFS pool can only be created on the local host (single-node management for now)",
            )
        if not payload.size_gb:
            raise HTTPException(status_code=422, detail="size_gb is required for a ZFS pool")
        size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
        if size_errors:
            raise HTTPException(status_code=422, detail=size_errors)
        if not zfs_storage.load_module():
            reason = zfs_storage.STATUS_MESSAGES[zfs_storage.status()]
            log_action(user["username"], "create_storage_pool", payload.name, "echec", reason)
            raise HTTPException(status_code=422, detail=reason)
        try:
            pool = zfs_storage.create_pool(payload.name, payload.size_gb)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "create_storage_pool", payload.name, "echec", e.message)
            raise HTTPException(status_code=500, detail=f"ZFS pool creation error: {e.message}") from e
        log_action(user["username"], "create_storage_pool", payload.name, "succes")
        return pool

    if payload.type == "dir":
        target_path = payload.path or str(nfs_permissions.POOLS_ROOT / payload.name)
        if not POOL_PATH_RE.match(target_path):
            raise HTTPException(
                status_code=422,
                detail="Invalid pool path (must be an absolute path, without spaces or special characters)",
            )
    elif payload.type == "iscsi":
        if not payload.iscsi_host or not payload.iscsi_target:
            raise HTTPException(status_code=422, detail="iscsi_host and iscsi_target are required for an iSCSI pool")
        if not NFS_HOST_RE.match(payload.iscsi_host):
            raise HTTPException(status_code=422, detail="Invalid iSCSI portal address")
        if not iscsi.IQN_RE.match(payload.iscsi_target):
            raise HTTPException(
                status_code=422,
                detail="Invalid iSCSI target name (expected an IQN such as iqn.2005-10.org.freenas.ctl:vms)",
            )
        if payload.chap_user and (not iscsi.CHAP_USER_RE.match(payload.chap_user) or not payload.chap_password):
            raise HTTPException(status_code=422, detail="CHAP needs a valid user name and a password")
        # libvirt drives open-iscsi: without it the error ("iscsiadm: not found") hides the real cause.
        if not node and not iscsi.initiator_available():
            raise HTTPException(
                status_code=422, detail="The iSCSI initiator is not installed on this host (open-iscsi package)"
            )
        target_path = iscsi.BY_PATH
    else:
        if not payload.nfs_host or not payload.nfs_export_path:
            raise HTTPException(status_code=422, detail="nfs_host and nfs_export_path are required for an NFS pool")
        if not NFS_HOST_RE.match(payload.nfs_host):
            raise HTTPException(status_code=422, detail="Invalid NFS host")
        if not POOL_PATH_RE.match(payload.nfs_export_path):
            raise HTTPException(status_code=422, detail="Invalid NFS export path (must be an absolute path)")
        # libvirt mounts the export with mount.nfs: without the NFS client its error ("mount: unknown filesystem
        # type") hides the real cause. Checked on the local host only (a remote node reports its own error).
        if not node and shutil.which("mount.nfs") is None:
            raise HTTPException(
                status_code=422, detail="The NFS client is not installed on this host (nfs-common package)"
            )
        # LOCAL mount point on the node (the NFS client side): distinct from the path
        # exported on the server side, and never supplied by the caller, to avoid any
        # collision with an existing system directory.
        target_path = str(nfs_permissions.POOLS_ROOT / payload.name)

    conn = open_conn(node)
    try:
        try:
            conn.storagePoolLookupByName(payload.name)
            log_action(user["username"], "create_storage_pool", payload.name, "echec", "Pool already exists")
            raise HTTPException(status_code=422, detail=f"A pool '{payload.name}' already exists")
        except libvirt.libvirtError:
            pass

        pool_xml = _build_pool_xml(payload, target_path)
        try:
            if payload.type == "iscsi" and payload.chap_user:
                # The CHAP password lives in a libvirt secret (private: never read back through the API), not in
                # the pool XML nor in Hyperlite's database.
                secret = conn.secretDefineXML(iscsi.secret_xml(payload.name))
                secret.setValue(payload.chap_password.encode())
            pool = conn.storagePoolDefineXML(pool_xml)
            # build() creates the local directory ("dir") or the mount point ("netfs"),
            # needed before create() on a brand new pool. flags=0: no destructive reformatting
            # of an existing medium. An iSCSI pool has nothing to build (the LUNs exist on the
            # storage side).
            if payload.type != "iscsi":
                pool.build(0)
            pool.create(0)
            pool.setAutostart(True)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "create_storage_pool", payload.name, "echec", msg)
            # Best-effort cleanup if the definition succeeded but the start did not (e.g. an
            # unreachable NFS export): avoids a "ghost" pool that is defined but never usable
            # and would block a new attempt with the same name.
            with contextlib.suppress(libvirt.libvirtError):
                conn.storagePoolLookupByName(payload.name).undefine()
            if payload.type == "iscsi":
                _delete_chap_secret(conn, payload.name)
                # libvirt only relays iscsiadm's command line: say what to check first.
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Could not log in to the iSCSI target: check the portal address and port, the target name, "
                        "that this host's initiator name is allowed on the storage side, and the CHAP credentials. "
                        f"Detail: {msg}"
                    ),
                ) from e
            raise HTTPException(status_code=500, detail=f"Pool creation error: {msg}") from e

        log_action(user["username"], "create_storage_pool", payload.name, "succes")
        summary = _pool_summary(pool)
        if payload.type == "netfs" and not node:
            # Mounted fine is not enough: QEMU must be able to own its disk files there (root_squash).
            perm = nfs_permissions.check(target_path, export=payload.nfs_export_path)
            summary["avertissement"] = perm["message"]
        return summary
    finally:
        conn.close()


def _delete_chap_secret(conn, pool_name):
    with contextlib.suppress(libvirt.libvirtError):
        conn.secretLookupByUsage(libvirt.VIR_SECRET_USAGE_TYPE_ISCSI, iscsi.secret_usage(pool_name)).undefine()


def _vms_using_paths(conn, paths):
    """Names of the VMs that have one of these exact device paths as a disk (the LUNs of an iSCSI pool)."""
    names = []
    for dom in conn.listAllDomains():
        try:
            xml = ET.fromstring(dom.XMLDesc(0))
        except libvirt.libvirtError:
            continue
        if any((src.get("dev") or src.get("file")) in paths for src in xml.findall("devices/disk/source")):
            names.append(dom.name())
    return names


def _vms_using_path(conn, target):
    """Names of the VMs that have a disk or CD-ROM under `target`."""
    if not target:
        return []
    prefix = target.rstrip("/") + "/"
    names = []
    for dom in conn.listAllDomains():
        try:
            xml = ET.fromstring(dom.XMLDesc(0))
        except libvirt.libvirtError:
            continue
        for src in xml.findall("devices/disk/source"):
            path = src.get("file") or src.get("dev") or ""
            if path.startswith(prefix):
                names.append(dom.name())
                break
    return names


@router.post("/{pool_name}/check-permissions")
def check_pool_permissions(pool_name: str, user: dict = Depends(require_role("admin"))):
    """For an NFS pool of this host: can QEMU own its disk files there (see app/core/nfs_permissions.py)?"""
    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        if _pool_type(pool) != "netfs":
            raise HTTPException(status_code=422, detail="Only NFS pools need this check")
        if not pool.isActive():
            raise HTTPException(status_code=409, detail="Start the pool first: the share must be mounted")
        export = ET.fromstring(pool.XMLDesc(0)).find("source/dir")
        result = nfs_permissions.check(
            _pool_path(pool), export=export.get("path") if export is not None else "/srv/share"
        )
        log_action(
            user["username"],
            "check_pool_permissions",
            pool_name,
            "succes" if result["ok"] else "echec",
            result["message"],
        )
        return {"nom": pool_name, "ok": result["ok"], "message": result["message"]}
    finally:
        conn.close()


@router.delete("/{pool_name}")
def delete_pool(
    pool_name: str,
    node: str | None = None,
    confirm: bool = False,
    detacher: bool = False,
    user: dict = Depends(require_role("admin")),
):
    if pool_name == "default":
        raise HTTPException(status_code=400, detail="The 'default' pool cannot be deleted")
    if not confirm:
        raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion")

    # A ZFS pool is NOT a libvirt pool (see zfs_storage.py): it is routed separately
    # before any lookup on the libvirt side, which would simply fail with "not found"
    # for a name that only exists on the ZFS side.
    if (not node or node == "local") and zfs_storage.pool_exists(pool_name):
        try:
            zfs_storage.delete_pool(pool_name)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "delete_storage_pool", pool_name, "echec", e.message)
            raise HTTPException(status_code=409, detail=e.message) from e
        log_action(user["username"], "delete_storage_pool", pool_name, "succes")
        return {"message": f"ZFS pool '{pool_name}' deleted"}

    conn = open_conn(node)
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None

        pool.refresh(0)
        volumes = pool.listAllVolumes()
        pool_root = ET.fromstring(pool.XMLDesc(0))
        if pool_root.get("type") == "iscsi":
            # Removing an iSCSI pool only logs out of the target: the LUNs and their data stay on the storage
            # side. Refused while a VM still uses one of them.
            in_use = _vms_using_paths(conn, {v.path() for v in volumes})
            if in_use:
                log_action(user["username"], "delete_storage_pool", pool_name, "echec", "LUNs used by VMs")
                raise HTTPException(
                    status_code=409,
                    detail=f"VMs use LUNs of this pool ({', '.join(in_use)}): delete them first",
                )
            volumes = []
        if volumes:
            # `detacher` only removes the libvirt pool DEFINITION (destroy + undefine), NEVER
            # the files: for a dir/netfs pool that is not destructive. A real case: a dir pool
            # created automatically by virt-install on /root sees all of /root as "volumes"
            # and could therefore never be deleted.
            root = ET.fromstring(pool.XMLDesc(0))
            if not detacher or root.get("type") not in ("dir", "netfs"):
                log_action(user["username"], "delete_storage_pool", pool_name, "echec", "Pool not empty")
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Pool '{pool_name}' still contains {len(volumes)} file(s)/volume(s). To remove the pool WITHOUT deleting these files, use the \"remove without deleting the files\" option (detacher=true, dir/NFS pools only)"
                    ),
                )
            in_use = _vms_using_path(conn, root.findtext("target/path"))
            if in_use:
                log_action(user["username"], "delete_storage_pool", pool_name, "echec", "Pool used by VMs")
                raise HTTPException(
                    status_code=409,
                    detail=f"VMs use files of this pool ({', '.join(in_use)}): remove them or move their disks first",
                )

        try:
            if pool.isActive():
                # netfs: unmounts the export. dir: does not touch the content of the directory
                # (already checked empty above). iscsi: logs out of the target.
                pool.destroy()
            pool.undefine()
            if pool_root.get("type") == "iscsi":
                _delete_chap_secret(conn, pool_name)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_storage_pool", pool_name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Pool deletion error: {msg}") from e

        log_action(user["username"], "delete_storage_pool", pool_name, "succes")
        return {"message": f"Pool '{pool_name}' deleted"}
    finally:
        conn.close()


@router.get("/{pool_name}/volumes")
def list_volumes(pool_name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        if zfs_storage.pool_exists(pool_name):
            in_use = get_disk_paths_in_use(conn)
            result = zfs_storage.list_zvols(pool_name)
            for vol in result:
                vol["utilise"] = vol["chemin"] in in_use
            log_action(user["username"], "list_volumes", pool_name, "succes")
            return result

        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        pool.refresh(0)
        in_use = get_disk_paths_in_use(conn)
        result = []
        for vol in pool.listAllVolumes():
            vol_info = vol.info()
            result.append(
                {
                    "nom": vol.name(),
                    "chemin": vol.path(),
                    "capacite_go": round(vol_info[1] / (1024**3), 3),
                    "allocation_go": round(vol_info[2] / (1024**3), 3),
                    "utilise": vol.path() in in_use,
                }
            )
        log_action(user["username"], "list_volumes", pool_name, "succes")
        return result
    finally:
        conn.close()


class VolumeCreate(BaseModel):
    name: str
    size_gb: int = Field(ge=1)


@router.post("/{pool_name}/volumes", status_code=201)
def create_volume(pool_name: str, payload: VolumeCreate, user: dict = Depends(require_role("admin"))):
    size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
    if size_errors:
        raise HTTPException(status_code=422, detail=size_errors)
    if zfs_storage.pool_exists(pool_name):
        name_error = zfs_storage.validate_zfs_name(payload.name)
        if name_error:
            log_action(user["username"], "create_volume", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)
        try:
            path = zfs_storage.create_zvol(pool_name, payload.name, payload.size_gb)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "create_volume", payload.name, "echec", e.message)
            raise HTTPException(status_code=500, detail=f"zvol creation error: {e.message}") from e
        log_action(user["username"], "create_volume", payload.name, "succes")
        return {"nom": payload.name, "chemin": path, "capacite_go": float(payload.size_gb)}

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_volume", payload.name, "echec", "Pool not found")
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        if _pool_type(pool) == "iscsi":
            raise HTTPException(
                status_code=422, detail="An iSCSI pool's LUNs are created and resized on the storage server, not here"
            )

        base_name = payload.name[: -len(".qcow2")] if payload.name.endswith(".qcow2") else payload.name
        name_error = validate_name(base_name, "volume")
        if name_error:
            log_action(user["username"], "create_volume", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)
        filename = f"{base_name}.qcow2"
        try:
            lookup_volume(pool, filename)
            log_action(user["username"], "create_volume", filename, "echec", "Volume already exists")
            raise HTTPException(status_code=422, detail=f"A volume '{filename}' already exists in this pool")
        except libvirt.libvirtError:
            pass

        size_bytes = payload.size_gb * (1024**3)
        vol_xml = f"""
        <volume>
          <name>{filename}</name>
          <capacity unit='bytes'>{size_bytes}</capacity>
          <target>
            <format type='qcow2'/>
          </target>
        </volume>
        """
        try:
            vol = pool.createXML(vol_xml, 0)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "create_volume", filename, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Volume creation error: {msg}") from e

        log_action(user["username"], "create_volume", filename, "succes")
        vol_info = vol.info()
        return {
            "nom": vol.name(),
            "chemin": vol.path(),
            "capacite_go": round(vol_info[1] / (1024**3), 3),
        }
    finally:
        conn.close()


@router.delete("/{pool_name}/volumes/{volume_name}")
def delete_volume(pool_name: str, volume_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if zfs_storage.pool_exists(pool_name):
        conn = open_conn()
        try:
            in_use = get_disk_paths_in_use(conn)
        finally:
            conn.close()
        if zfs_storage.device_path(pool_name, volume_name) in in_use:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume used by a VM")
            raise HTTPException(status_code=409, detail=f"Volume '{volume_name}' is used by a VM, deletion refused")
        if not confirm:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Confirmation manquante")
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )
        try:
            zfs_storage.delete_zvol(pool_name, volume_name)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "delete_volume", volume_name, "echec", e.message)
            raise HTTPException(
                status_code=404 if isinstance(e, zfs_storage.ZfsNotFoundError) else 500, detail=e.message
            ) from e
        log_action(user["username"], "delete_volume", volume_name, "succes")
        return {"message": f"Volume '{volume_name}' deleted"}

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        if _pool_type(pool) == "iscsi":
            raise HTTPException(
                status_code=422, detail="An iSCSI pool's LUNs are deleted on the storage server, not here"
            )
        try:
            vol = lookup_volume(pool, volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume not found")
            raise HTTPException(status_code=404, detail=f"Volume '{volume_name}' not found") from None

        in_use = get_disk_paths_in_use(conn)
        if vol.path() in in_use:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume used by a VM")
            raise HTTPException(status_code=409, detail=f"Volume '{volume_name}' is used by a VM, deletion refused")

        if not confirm:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Confirmation manquante")
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )

        try:
            vol.delete(0)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_volume", volume_name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Deletion error: {msg}") from e

        log_action(user["username"], "delete_volume", volume_name, "succes")
        return {"message": f"Volume '{volume_name}' deleted"}
    finally:
        conn.close()
