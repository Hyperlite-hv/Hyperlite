"""The libvirt storage pools the storage routes make, rename and remove (app/routers/storage.py): a directory, an NFS
export, an SMB share, an iSCSI target, an LVM volume group.

Each function takes the libvirt connection of the node it acts on. This node's work is hyperlite-daemon's
(app/daemon/ops/storage.py, `libvirt_utils.local_conn`); another node's is the API's, over that node's SSH connection,
until the cluster domain moves it (docs/design/privilege-separation.md, section 13). Both paths run the same checks and
give the same answers. A refusal or a failure is a PoolError, whose kind is one of the daemon's (app/daemon/__init__.py):
the route answers it the same way whichever path it took.

A pool's definition is written here from checked fields, never from XML a caller sends."""

import contextlib
import logging
import re
import shutil
import socket
import subprocess
import xml.etree.ElementTree as ET
from xml.sax import saxutils

import libvirt

from app.core import iscsi, lvm_storage, nfs_permissions, smb_storage
from app.core.error_messages import describe_exception

logger = logging.getLogger(__name__)

# Host name/IP (NFS pool): letters/digits/dots/dashes. Enough for a hostname or
# a simple IPv4/IPv6 address, and it excludes any character that could have a
# special meaning elsewhere.
NFS_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.:_-]{0,253})$")
# Absolute path (server-side NFS export, or the local directory of a "dir"
# pool): no spaces or special XML/shell characters.
POOL_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,255}$")
# "lvm" pool built on a disk: a device under /dev (a whole disk or a partition), never a path with "..".
LVM_DEVICE_RE = re.compile(r"^/dev/(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9/_.:-]{0,200}$")
# Every file in a "dir" pool's directory is listed as a volume, and a volume can be deleted from the dashboard: a
# pool on /etc (accepted until the audit before 1.0.0) offered /etc/passwd for deletion. Refused, with everything
# below them.
SYSTEM_TREES = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/proc", "/root", "/run", "/sbin", "/sys",
    "/usr", "/var/lib/hyperlite", "/var/lib/apt", "/var/lib/dpkg", "/var/log",
    "/tmp",  # noqa: S108 - emptied at boot (a tmpfs on many hosts): the disks would vanish
)  # fmt: skip
# Refused as such, a directory below them is fine (/home/vms, /srv/pool, /mnt/disk2).
SYSTEM_DIRS = ("/", "/home", "/media", "/mnt", "/opt", "/srv", "/var", "/var/lib", "/var/lib/libvirt")

POOL_TYPES = ("dir", "netfs", "smb", "iscsi", "lvm")  # "zfs" is not libvirt's (app/core/zfs_storage.py)


class PoolError(Exception):
    """A refusal or a failure, with the daemon's kind and a sentence for the user."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def normalize(path):
    return "/" + "/".join(p for p in path.split("/") if p)


def dir_path_error(path):
    """Why a "dir" pool may not use this directory, or None."""
    parts = path.split("/")
    if ".." in parts or "." in parts:
        return "Invalid pool path: '.' and '..' are not allowed"
    path = normalize(path)
    if path in SYSTEM_DIRS or any(path == t or path.startswith(t + "/") for t in SYSTEM_TREES):
        return f"{path} is a system directory: its files would be listed as volumes, and could be deleted"
    return None


def _inside(a, b):
    return a == b or a.startswith(b.rstrip("/") + "/")


def pool_type(pool):
    try:
        root = ET.fromstring(pool.XMLDesc(0))
        return root.get("type", "unknown")
    except (libvirt.libvirtError, ET.ParseError):
        return "unknown"


def pool_path(pool):
    try:
        return ET.fromstring(pool.XMLDesc(0)).findtext("target/path")
    except (libvirt.libvirtError, ET.ParseError):
        return None


def pool_on_path(conn, path):
    """The pool whose directory is this one, or contains it, or lies inside it (libvirt only catches the first)."""
    for pool in conn.listAllStoragePools(0):
        other = pool_path(pool)
        if other and other.startswith("/") and (_inside(path, normalize(other)) or _inside(normalize(other), path)):
            return pool.name()
    return None


def _refuse(message):
    raise PoolError("refused", message)


def check_spec(spec, local=True):
    """The checks of a new libvirt pool's fields, whoever runs it: the route first, then hyperlite-daemon again. Returns
    the pool's target path. `local`: the pool is made on this host, whose tools are checked here (another node
    reports its own error). The name is checked by the caller (vm_builder.validate_name)."""
    if spec.type not in POOL_TYPES:
        _refuse(f"Unknown pool type: {spec.type}")
    if spec.type == "dir":
        target_path = spec.path or str(nfs_permissions.POOLS_ROOT / spec.name)
        if not POOL_PATH_RE.match(target_path):
            _refuse("Invalid pool path (must be an absolute path, without spaces or special characters)")
        path_error = dir_path_error(target_path)
        if path_error:
            _refuse(path_error)
        return normalize(target_path)
    if spec.type == "iscsi":
        if not spec.iscsi_host or not spec.iscsi_target:
            _refuse("iscsi_host and iscsi_target are required for an iSCSI pool")
        if not NFS_HOST_RE.match(spec.iscsi_host):
            _refuse("Invalid iSCSI portal address")
        if not iscsi.IQN_RE.match(spec.iscsi_target):
            _refuse("Invalid iSCSI target name (expected an IQN such as iqn.2005-10.org.freenas.ctl:vms)")
        # CHAP is a user AND a password: a password alone was silently dropped, and the pool created without CHAP.
        if (spec.chap_user or spec.chap_password) and (
            not iscsi.CHAP_USER_RE.match(spec.chap_user or "") or not spec.chap_password
        ):
            _refuse("CHAP needs both a valid user name and a password")
        # libvirt drives open-iscsi: without it the error ("iscsiadm: not found") hides the real cause.
        if local and not iscsi.initiator_available():
            _refuse("The iSCSI initiator is not installed on this host (open-iscsi package)")
        return iscsi.BY_PATH
    if spec.type == "lvm":
        try:
            lvm_storage.check_name(spec.lvm_vg or "")
            if spec.lvm_thin_pool is not None:
                lvm_storage.check_name(spec.lvm_thin_pool, "thin pool")
        except lvm_storage.LvmError as e:
            _refuse(str(e))
        if spec.lvm_device and not LVM_DEVICE_RE.match(spec.lvm_device):
            _refuse("Invalid disk: a device under /dev, such as /dev/sdb")
        if local and shutil.which("lvm") is None:
            _refuse("LVM is not installed on this host (lvm2 package)")
        return f"/dev/{spec.lvm_vg}"
    if spec.type == "smb":
        error = smb_storage.validation_error(
            bool(spec.smb_host and NFS_HOST_RE.match(spec.smb_host)),
            spec.smb_share,
            spec.smb_user,
            spec.smb_password,
            spec.smb_domain,
            spec.smb_version,
        )
        if error:
            _refuse(error)
        # libvirt mounts the share with mount.cifs: without it its error hides the real cause (this host only).
        if local and shutil.which("mount.cifs") is None:
            _refuse("The SMB client is not installed on this host (cifs-utils package)")
        return str(nfs_permissions.POOLS_ROOT / spec.name)
    if not spec.nfs_host or not spec.nfs_export_path:
        _refuse("nfs_host and nfs_export_path are required for an NFS pool")
    if not NFS_HOST_RE.match(spec.nfs_host):
        _refuse("Invalid NFS host")
    if not POOL_PATH_RE.match(spec.nfs_export_path):
        _refuse("Invalid NFS export path (must be an absolute path)")
    # libvirt mounts the export with mount.nfs: without the NFS client its error ("mount: unknown filesystem
    # type") hides the real cause. Checked on the local host only (a remote node reports its own error).
    if local and shutil.which("mount.nfs") is None:
        _refuse("The NFS client is not installed on this host (nfs-common package)")
    # LOCAL mount point on the node (the NFS client side): distinct from the path
    # exported on the server side, and never supplied by the caller, to avoid any
    # collision with an existing system directory.
    return str(nfs_permissions.POOLS_ROOT / spec.name)


def pool_xml(spec, target_path: str, cred_path: str | None = None) -> str:
    """Build the libvirt XML with ElementTree (automatic escaping) rather than by
    string concatenation: the security audit had found an XML injection in
    bridge-mode network creation for exactly that reason, and the mistake must
    not be repeated here."""
    pool_el = ET.Element("pool", type={"smb": "netfs", "lvm": "logical"}.get(spec.type, spec.type))
    ET.SubElement(pool_el, "name").text = spec.name
    if spec.type == "lvm":
        source_el = ET.SubElement(pool_el, "source")
        if spec.lvm_device:
            ET.SubElement(source_el, "device", path=spec.lvm_device)
        ET.SubElement(source_el, "name").text = spec.lvm_vg
        ET.SubElement(source_el, "format", type="lvm2")
        ET.SubElement(ET.SubElement(pool_el, "target"), "path").text = target_path
        return ET.tostring(pool_el, encoding="unicode")
    if spec.type == "iscsi":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=spec.iscsi_host, port=str(spec.iscsi_port))
        ET.SubElement(source_el, "device", path=spec.iscsi_target)
        if spec.chap_user:
            auth_el = ET.SubElement(source_el, "auth", type="chap", username=spec.chap_user)
            ET.SubElement(auth_el, "secret", usage=iscsi.secret_usage(spec.name))
        target_el = ET.SubElement(pool_el, "target")
        ET.SubElement(target_el, "path").text = iscsi.BY_PATH
        return ET.tostring(pool_el, encoding="unicode")
    if spec.type == "smb":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=spec.smb_host)
        ET.SubElement(source_el, "dir", path=spec.smb_share)
        ET.SubElement(source_el, "format", type="cifs")
        # The same local-namespace form as the NFS options below (the only one libvirt honours), every value quoted.
        mount_opts_xml = (
            '<mount_opts xmlns="http://libvirt.org/schemas/storagepool/fs/1.0">'
            + "".join(
                f"<option name={saxutils.quoteattr(o)}/>"
                for o in smb_storage.mount_options(cred_path, spec.smb_version)
            )
            + "</mount_opts>"
        )
    elif spec.type == "netfs":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=spec.nfs_host)
        ET.SubElement(source_el, "dir", path=spec.nfs_export_path)
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
        # purpose libvirt on this version still ignores it silently (the element is absent
        # from the XML read back right after defineXML). Only the form with "xmlns=..."
        # on the element itself (a LOCAL default namespace, not a root prefix) is honoured,
        # and ElementTree never generates that exact form. The rest of the document is
        # still built with ElementTree (automatic escaping, see above); only this fragment
        # is assembled as a string, with values that are already validated (NFS_HOST_RE
        # above) or resolved through gethostbyname, never raw user text.
        try:
            addr = socket.gethostbyname(spec.nfs_host)
        except OSError:
            addr = spec.nfs_host  # resolution failed: try anyway with the value as provided
        mount_opts_xml = (
            f'<mount_opts xmlns="http://libvirt.org/schemas/storagepool/fs/1.0">'
            f'<option name="addr={saxutils.escape(addr)}"/><option name="vers={spec.nfs_version}"/></mount_opts>'
        )
    else:
        mount_opts_xml = ""
    target_el = ET.SubElement(pool_el, "target")
    ET.SubElement(target_el, "path").text = target_path
    xml = ET.tostring(pool_el, encoding="unicode")
    if mount_opts_xml:
        xml = xml.replace("</source>", "</source>" + mount_opts_xml, 1)
    return xml


def chap_usage(pool_root):
    """The usage id of the CHAP secret a pool's definition refers to: a renamed pool keeps its secret's."""
    secret = pool_root.find("source/auth/secret")
    return secret.get("usage") if secret is not None else None


def delete_chap_secret(conn, pool_name, usage=None):
    with contextlib.suppress(libvirt.libvirtError):
        conn.secretLookupByUsage(libvirt.VIR_SECRET_USAGE_TYPE_ISCSI, usage or iscsi.secret_usage(pool_name)).undefine()


def create(conn, spec, node=None):
    """Define, build and start the pool on the node of `conn` (`node`: another node's name, None for this host), with
    its SMB credentials, its CHAP secret and its LVM thin pool. Returns {"warning"}: on this host, whether QEMU can own
    files on a share it mounted. Nothing is left half made."""
    target_path = check_spec(spec, local=node is None)
    if spec.type == "dir":
        other = pool_on_path(conn, target_path)
        if other:
            raise PoolError("in_use", f"The pool '{other}' already uses {target_path}: choose another directory")
    try:
        conn.storagePoolLookupByName(spec.name)
        _refuse(f"A pool '{spec.name}' already exists")
    except libvirt.libvirtError:
        pass

    cred_path = smb_storage.credentials_path(spec.name, node) if spec.type == "smb" else None
    xml = pool_xml(spec, target_path, cred_path)
    try:
        if cred_path:
            try:
                smb_storage.write_credentials(
                    cred_path, smb_storage.credentials_text(spec.smb_user, spec.smb_password, spec.smb_domain), node
                )
            except (OSError, subprocess.SubprocessError) as e:
                raise PoolError("unreachable", f"The SMB credentials could not be written: {e}") from e
        if spec.type == "iscsi" and spec.chap_user:
            # The CHAP password lives in a libvirt secret (private: never read back through the API), not in
            # the pool XML nor in Hyperlite's database.
            secret = conn.secretDefineXML(iscsi.secret_xml(spec.name))
            secret.setValue(spec.chap_password.encode())
        pool = conn.storagePoolDefineXML(xml)
        # build() creates the local directory ("dir") or the mount point ("netfs"),
        # needed before create() on a brand new pool. flags=0: no destructive reformatting
        # of an existing medium. An iSCSI pool has nothing to build (the LUNs exist on the
        # storage side), nor an LVM pool on an existing volume group; on a disk, build() makes the group.
        if spec.type != "iscsi" and not (spec.type == "lvm" and not spec.lvm_device):
            pool.build(0)
        pool.create(0)
        pool.setAutostart(True)
    except libvirt.libvirtError as e:
        msg = describe_exception(e)
        # Best-effort cleanup if the definition succeeded purpose the start did not (e.g. an
        # unreachable NFS export): avoids a "ghost" pool that is defined purpose never usable
        # and would block a new attempt with the same name.
        with contextlib.suppress(libvirt.libvirtError):
            conn.storagePoolLookupByName(spec.name).undefine()
        if cred_path:
            smb_storage.remove_credentials(cred_path, node)
            raise PoolError(
                "unreachable",
                "Could not mount the SMB share: check the server address, the share name, the user and "
                f"password, and the SMB version. Detail: {msg}",
            ) from e
        if spec.type == "iscsi":
            delete_chap_secret(conn, spec.name)
            # libvirt only relays iscsiadm's command line: say what to check first.
            raise PoolError(
                "unreachable",
                "Could not log in to the iSCSI target: check the portal address and port, the target name, "
                "that this host's initiator name is allowed on the storage side, and the CHAP credentials. "
                f"Detail: {msg}",
            ) from e
        raise PoolError("failed", f"Pool creation error: {msg}") from e

    if spec.type == "lvm" and spec.lvm_thin_pool:
        try:
            lvm_storage.ensure_thin_pool(spec.lvm_vg, spec.lvm_thin_pool, node)
        except lvm_storage.LvmError as e:
            # The pool is not left half made: its group stays as it is (a group built on a disk included).
            with contextlib.suppress(libvirt.libvirtError):
                pool.destroy()
            with contextlib.suppress(libvirt.libvirtError):
                pool.undefine()
            _refuse(f"Thin pool: {e}")

    warning = None
    if node is None and spec.type == "netfs":
        # Mounted fine is not enough: QEMU must be able to own its disk files there (root_squash).
        warning = nfs_permissions.check(target_path, export=spec.nfs_export_path)["message"]
    elif node is None and spec.type == "smb":
        warning = smb_storage.check(target_path)["message"]
    return {"warning": warning}


def _lookup(conn, name):
    try:
        return conn.storagePoolLookupByName(name)
    except libvirt.libvirtError:
        raise PoolError("not_found", f"Storage pool '{name}' not found") from None


def vms_using_paths(conn, paths):
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


def vms_using_path(conn, target):
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


def running_users(conn, pool):
    """Running VMs with a disk in this pool: its files (directory, NFS) or its LUNs (iSCSI)."""
    root = ET.fromstring(pool.XMLDesc(0))
    if root.get("type") == "iscsi":
        with contextlib.suppress(libvirt.libvirtError):
            pool.refresh(0)
        paths = {v.path() for v in pool.listAllVolumes()}
        match = lambda p: p in paths  # noqa: E731
    else:
        target = (root.findtext("target/path") or "").rstrip("/")
        if not target:
            return []
        match = lambda p: p.startswith(target + "/")  # noqa: E731
    names = []
    for dom in conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE):
        try:
            xml = ET.fromstring(dom.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError):
            continue
        if any(match(src.get("dev") or src.get("file") or "") for src in xml.findall("devices/disk/source")):
            names.append(dom.name())
    return names


def rename(conn, pool_name, new):
    """libvirt cannot rename a pool: it is stopped, redefined under the new name with the same source and target,
    and started again. Its files and LUNs do not move, so the VMs' disk paths stay valid; an iSCSI pool keeps its
    CHAP secret. Refused while a running VM uses it (stopping the pool would pull its disks away)."""
    pool = _lookup(conn, pool_name)
    try:
        conn.storagePoolLookupByName(new)
        _refuse(f"A pool named '{new}' already exists")
    except libvirt.libvirtError:
        logger.debug("No pool named %s yet", new)
    in_use = running_users(conn, pool)
    if in_use:
        raise PoolError(
            "in_use",
            f"Running VMs use this pool ({', '.join(in_use)}): stop them first, the pool is stopped "
            "for a moment while it is renamed",
        )
    old_xml = pool.XMLDesc(libvirt.VIR_STORAGE_XML_INACTIVE)
    was_active, autostart = bool(pool.isActive()), bool(pool.autostart())
    root = ET.fromstring(old_xml)
    root.find("name").text = new
    uuid = root.find("uuid")
    if uuid is not None:
        root.remove(uuid)  # a new definition: libvirt gives it its own
    try:
        if was_active:
            pool.destroy()
        pool.undefine()
        try:
            renamed = conn.storagePoolDefineXML(ET.tostring(root, encoding="unicode"))
            if was_active:
                renamed.create(0)
            renamed.setAutostart(autostart)
        except libvirt.libvirtError:
            with contextlib.suppress(libvirt.libvirtError):
                conn.storagePoolLookupByName(new).undefine()
            restored = conn.storagePoolDefineXML(old_xml)
            if was_active:
                restored.create(0)
            restored.setAutostart(autostart)
            raise
    except libvirt.libvirtError as e:
        raise PoolError("failed", f"Rename failed: {describe_exception(e)}") from e


def delete(conn, pool_name, detach=False, node=None):
    """Remove the pool from the node of `conn`: its definition, its CHAP secret, its SMB credentials. Never the user's
    files: refused while it holds any, unless `detach` (a directory or NFS pool: only the definition goes)."""
    from app.core.vm_builder import POOL_BASE_NAME

    pool = _lookup(conn, pool_name)
    pool.refresh(0)
    volumes = pool.listAllVolumes()
    pool_root = ET.fromstring(pool.XMLDesc(0))
    if pool_root.get("type") == "logical":
        # libvirt does not see thin volumes nor thin pools: it would call a group holding only those empty and
        # stop the pool under them. LVM itself is asked; libvirt's pool deletion (the whole group) is never used.
        try:
            lvm_storage.refuse_removal_if_not_empty(pool_root.findtext("source/name"), node)
        except lvm_storage.LvmError as e:
            raise PoolError("in_use", str(e)) from e
    # The base image Hyperlite copied into the pool for the VMs built on it (vm_builder.ensure_pool_base_image)
    # is its own copy, not a file of the user's: it does not keep the pool from being removed. A pool where it
    # is the last file therefore said "still contains 1 file" for a pool the user had emptied.
    base_images = [v for v in volumes if v.name() == POOL_BASE_NAME]
    volumes = [v for v in volumes if v.name() != POOL_BASE_NAME]
    if pool_root.get("type") == "iscsi":
        # Removing an iSCSI pool only logs out of the target: the LUNs and their data stay on the storage
        # side. Refused while a VM still uses one of them.
        in_use = vms_using_paths(conn, {v.path() for v in volumes})
        if in_use:
            raise PoolError("in_use", f"VMs use LUNs of this pool ({', '.join(in_use)}): delete them first")
        volumes = []
    if volumes:
        # `detach` only removes the libvirt pool DEFINITION (destroy + undefine), NEVER
        # the files: for a dir/netfs pool that is not destructive. A real case: a dir pool
        # created automatically by virt-install on /root sees all of /root as "volumes"
        # and could therefore never be deleted.
        if not detach or pool_root.get("type") not in ("dir", "netfs"):
            raise PoolError(
                "in_use",
                f"Pool '{pool_name}' still contains {len(volumes)} file(s)/volume(s). To remove the pool WITHOUT "
                'deleting these files, use the "remove without deleting the files" option (detach=true, dir/NFS '
                "pools only)",
            )
        in_use = vms_using_path(conn, pool_root.findtext("target/path"))
        if in_use:
            raise PoolError(
                "in_use", f"VMs use files of this pool ({', '.join(in_use)}): remove them or move their disks first"
            )

    # Removed with a pool of this node; kept on a shared one (NFS, SMB), where the VMs of the other nodes using
    # the same share may still be built on it.
    if pool_root.get("type") == "dir" and not detach:
        in_use = vms_using_path(conn, pool_root.findtext("target/path")) if base_images else []
        if in_use:
            raise PoolError(
                "in_use", f"VMs use files of this pool ({', '.join(in_use)}): remove them or move their disks first"
            )
        for volume in base_images:
            volume.delete(0)
    try:
        if pool.isActive():
            # netfs: unmounts the export. dir: does not touch the content of the directory
            # (already checked empty above). iscsi: logs out of the target.
            pool.destroy()
        pool.undefine()
        if pool_root.get("type") == "iscsi":
            delete_chap_secret(conn, pool_name, chap_usage(pool_root))
        cred_path = smb_storage.credentials_of(pool_root) if smb_storage.is_smb(pool_root) else None
        if cred_path:
            smb_storage.remove_credentials(cred_path, node)
    except libvirt.libvirtError as e:
        raise PoolError("failed", f"Pool deletion error: {describe_exception(e)}") from e


def check_permissions(conn, pool_name):
    """For an NFS or SMB pool of this host: can QEMU own its disk files there (app/core/nfs_permissions.py)? A file
    is written into the share and removed: {"ok", "message"}."""
    pool = _lookup(conn, pool_name)
    if pool_type(pool) != "netfs":
        _refuse("Only NFS and SMB pools need this check")
    if not pool.isActive():
        raise PoolError("in_use", "Start the pool first: the share must be mounted")
    root = ET.fromstring(pool.XMLDesc(0))
    if smb_storage.is_smb(root):
        return smb_storage.check(pool_path(pool))
    export = root.find("source/dir")
    return nfs_permissions.check(pool_path(pool), export=export.get("path") if export is not None else "/srv/share")
