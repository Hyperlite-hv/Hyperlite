import os
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import HTTPException

LIBVIRT_URI = "qemu:///system"
LXC_URI = "lxc:///system"


def open_lxc_conn():
    """Connection dedicated to containers: libvirt exposes LXC through a driver/URI
    SEPARATE from qemu:///system (same libvirtd daemon, but VM and container
    domains do not share the same list: a qemu:///system connection never
    sees a container, and vice versa). There is no multi-node support for now
    (unlike open_conn): local containers only in this first version."""
    conn = libvirt.open(LXC_URI)
    if conn is None:
        raise HTTPException(status_code=500, detail="Unable to open the libvirt (LXC) connection")
    return conn


def open_conn(node_name=None):
    """node_name=None (the default): unchanged local connection, so every existing
    call (dozens, across all routers) keeps working without any change.
    node_name='<registered name>': remote connection through qemu+ssh://
    (see app/core/cluster.py) to a registered cluster node."""
    if node_name and node_name != "local":
        from app.core.cluster import build_libvirt_uri, get_node

        node = get_node(node_name)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{node_name}' not found")
        uri = build_libvirt_uri(node)
    else:
        uri = LIBVIRT_URI
    conn = libvirt.open(uri)
    if conn is None:
        raise HTTPException(status_code=500, detail="Unable to open the libvirt connection")
    return conn


def get_vm_uptime_s(vm_name):
    """Time since the start of this VM's qemu PROCESS (not the guest OS's internal
    uptime, which libvirt does not expose without qemu-guest-agent; same
    convention as Proxmox). Reads the PID file that libvirt writes for every
    active domain, then the "starttime" field of /proc/<pid>/stat (field 22,
    in clock ticks since the HOST boot) to derive the process age by
    difference with /proc/uptime. Returns None when the VM is stopped or the
    information cannot be read (not a blocking error, just an unknown uptime
    shown in degraded form on the dashboard)."""
    try:
        pid = int(Path(f"/run/libvirt/qemu/{vm_name}.pid").read_text().strip())
        stat = Path(f"/proc/{pid}/stat").read_text()
        # The process name (2nd field) is in parentheses and may contain spaces, so we
        # restart from the last ')' to find the following fields reliably instead of
        # naively splitting on ' '.
        after_comm = stat.rsplit(")", 1)[1].split()
        starttime_ticks = int(after_comm[22 - 3])  # field 22; the state (field 3) is after_comm[0]
        clk_tck = os.sysconf("SC_CLK_TCK")
        host_uptime_s = float(Path("/proc/uptime").read_text().split()[0])
        uptime = host_uptime_s - (starttime_ticks / clk_tck)
        return int(uptime) if uptime >= 0 else None
    except (OSError, ValueError, IndexError):
        return None


def ensure_default_pool(conn):
    """Create and start the 'default' storage pool if it does not exist yet."""
    try:
        pool = conn.storagePoolLookupByName("default")
    except libvirt.libvirtError:
        pool_xml = """
        <pool type='dir'>
          <name>default</name>
          <target>
            <path>/var/lib/libvirt/images</path>
          </target>
        </pool>
        """
        pool = conn.storagePoolDefineXML(pool_xml)
        pool.build()
    if not pool.isActive():
        pool.create()
    pool.setAutostart(True)
    return pool


def pool_type_and_target_path(pool):
    """(type, target path) of a storage pool, or (None, None) if unreadable. Shared
    between migration and HA, both needing to know whether a disk lives on
    SHARED storage (a 'netfs' pool)."""
    try:
        root = ET.fromstring(pool.XMLDesc(0))
        return root.get("type"), root.findtext("target/path")
    except (libvirt.libvirtError, ET.ParseError):
        return None, None


def domain_disk_paths(domain):
    """Paths of all FILE disks (device='disk', not CD-ROMs) of a domain. CD-ROMs and
    ISOs are deliberately ignored: they are never considered "shared" storage
    in the sense of this project."""
    root = ET.fromstring(domain.XMLDesc(0))
    paths = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("device") != "disk":
            continue
        source_el = disk_el.find("source")
        path = source_el.get("file") if source_el is not None else None
        if path:
            paths.append(path)
    return paths


def uses_shared_storage(src_conn, dest_conn, domain):
    """True only if EVERY disk of the VM lives on a 'netfs' pool that exists AND is
    active on `dest_conn`, UNDER THE SAME NAME. That is sufficient in practice
    because the local mount path of a netfs pool is always derived from the
    pool name (see create_pool, app/routers/storage.py), so the same name on
    both sides implies the same NFS export mounted at the same place. Used by
    live migration (where 'dest_conn' is the real target node) AND by HA
    (where it is a PRECONDITION of protection: a VM whose disk is not shared
    cannot be recovered on another node if the source node really fails)."""
    disk_paths = domain_disk_paths(domain)
    if not disk_paths:
        return True  # nothing to copy or share (a VM without a file disk, rare)

    src_pools = list(src_conn.listAllStoragePools())
    for path in disk_paths:
        matched = None
        for pool in src_pools:
            pool_type, target_path = pool_type_and_target_path(pool)
            if target_path and path.startswith(target_path.rstrip("/") + "/"):
                matched = (pool.name(), pool_type)
                break
        if not matched or matched[1] != "netfs":
            return False
        pool_name, _ = matched
        try:
            dest_pool = dest_conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            return False
        dest_type, _ = pool_type_and_target_path(dest_pool)
        if dest_type != "netfs" or not dest_pool.isActive():
            return False
    return True


def get_disk_paths_in_use(conn):
    """Return the set of disk file paths currently referenced by at least one VM
    (running or not), to prevent deleting a volume that is in use."""
    paths = set()
    for domain in conn.listAllDomains():
        try:
            xml_desc = domain.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            for disk in root.findall(".//devices/disk"):
                source = disk.find("source")
                if source is not None:
                    p = source.get("file") or source.get("dev")
                    if p:
                        paths.add(p)
        except libvirt.libvirtError:
            continue
    return paths


def ensure_isolated_network(conn):
    """Create and start an isolated demo network if it does not exist yet (no
    <forward> element => no external connectivity, useful to tell NAT / bridge
    / isolated apart)."""
    try:
        net = conn.networkLookupByName("hyperlite-isolated")
    except libvirt.libvirtError:
        net_xml = """
        <network>
          <name>hyperlite-isolated</name>
          <bridge name='virbr-hlisol' stp='on' delay='0'/>
          <ip address='192.168.100.1' netmask='255.255.255.0'>
            <dhcp>
              <range start='192.168.100.10' end='192.168.100.100'/>
            </dhcp>
          </ip>
        </network>
        """
        net = conn.networkDefineXML(net_xml)
    if not net.isActive():
        net.create()
    net.setAutostart(True)
    return net


def ensure_vnc_graphics(conn, domain):
    # Make sure a domain has a VNC graphics device. If it is missing and the domain
    # is stopped, add it and redefine the domain. Returns True if a change was made,
    # False otherwise (already present, or domain running: reliably adding one on
    # the fly is not possible).
    root = ET.fromstring(domain.XMLDesc(0))
    devices_el = root.find(".//devices")
    if devices_el is None:
        return False
    existing = devices_el.find("graphics[@type='vnc']")
    if existing is not None:
        return False
    if domain.isActive():
        return False
    graphics_el = ET.SubElement(devices_el, "graphics")
    graphics_el.set("type", "vnc")
    graphics_el.set("port", "-1")
    graphics_el.set("autoport", "yes")
    graphics_el.set("listen", "127.0.0.1")
    listen_el = ET.SubElement(graphics_el, "listen")
    listen_el.set("type", "address")
    listen_el.set("address", "127.0.0.1")
    new_xml = ET.tostring(root, encoding="unicode")
    conn.defineXML(new_xml)
    return True
