import libvirt
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
