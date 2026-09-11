import libvirt
import xml.etree.ElementTree as ET
from fastapi import HTTPException

LIBVIRT_URI = "qemu:///system"


def open_conn():
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise HTTPException(status_code=500, detail="Connexion libvirt impossible")
    return conn


def ensure_default_pool(conn):
    """Cree et demarre le pool de stockage 'default' s'il n'existe pas deja."""
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


def get_disk_paths_in_use(conn):
    """Retourne l'ensemble des chemins de fichiers disque actuellement references
    par au moins une VM (active ou non), pour empecher la suppression d'un volume utilise."""
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
    """Cree et demarre un reseau isole de demonstration s'il n'existe pas deja
    (aucune balise <forward> => pas de connectivite externe, utile pour distinguer
    NAT / bridge / isole)."""
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
