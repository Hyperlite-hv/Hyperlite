import os
import libvirt
import xml.etree.ElementTree as ET
from fastapi import HTTPException

LIBVIRT_URI = "qemu:///system"
LXC_URI = "lxc:///system"


def open_lxc_conn():
    """Connexion dediee aux conteneurs (chantier 18) : libvirt expose LXC via
    une URI/pilote SEPARE de qemu:///system (meme demon libvirtd, mais les
    domaines VM et conteneur ne partagent pas la meme liste -- un
    connexion qemu:///system ne verra jamais un conteneur, et inversement).
    Pas de support multi-noeud pour l'instant (contrairement a open_conn) :
    conteneurs locaux uniquement dans cette premiere version."""
    conn = libvirt.open(LXC_URI)
    if conn is None:
        raise HTTPException(status_code=500, detail="Connexion libvirt (LXC) impossible")
    return conn


def open_conn(node_name=None):
    """node_name=None (par defaut) : connexion locale inchangee, EXACTEMENT
    comme avant le chantier 15 -- tous les appels existants (des dizaines,
    dans tous les routers) continuent de fonctionner sans aucune
    modification. node_name='<nom enregistre>' : connexion distante via
    qemu+ssh:// (voir app/core/cluster.py) vers un noeud du chantier 15."""
    if node_name and node_name != "local":
        from app.core.cluster import build_libvirt_uri, get_node
        node = get_node(node_name)
        if not node:
            raise HTTPException(status_code=404, detail=f"Nœud '{node_name}' introuvable")
        uri = build_libvirt_uri(node)
    else:
        uri = LIBVIRT_URI
    conn = libvirt.open(uri)
    if conn is None:
        raise HTTPException(status_code=500, detail="Connexion libvirt impossible")
    return conn


def get_vm_uptime_s(vm_name):
    """Duree depuis le demarrage du PROCESSUS qemu de cette VM (pas l'uptime
    interne de l'OS invite, que libvirt n'expose pas sans qemu-guest-agent --
    meme convention que Proxmox). Lit le fichier PID que libvirt ecrit pour
    chaque domaine actif, puis le champ "starttime" de /proc/<pid>/stat
    (22e champ, en ticks d'horloge depuis le boot de l'HOTE) pour en deduire
    l'age du processus par difference avec /proc/uptime. Retourne None si la
    VM est arretee ou si l'info n'est pas lisible (pas une erreur bloquante,
    juste un uptime inconnu affiche en degrade cote dashboard)."""
    try:
        pid = int(open(f"/run/libvirt/qemu/{vm_name}.pid").read().strip())
        stat = open(f"/proc/{pid}/stat").read()
        # Le nom du process (2e champ) est entre parentheses et peut contenir
        # des espaces -- on repart du dernier ')' pour retrouver les champs
        # suivants de facon fiable plutot que de decouper naivement sur ' '.
        after_comm = stat.rsplit(")", 1)[1].split()
        starttime_ticks = int(after_comm[22 - 3])  # champ 22, l'etat (champ 3) est after_comm[0]
        clk_tck = os.sysconf("SC_CLK_TCK")
        host_uptime_s = float(open("/proc/uptime").read().split()[0])
        uptime = host_uptime_s - (starttime_ticks / clk_tck)
        return int(uptime) if uptime >= 0 else None
    except (OSError, ValueError, IndexError):
        return None


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


def pool_type_and_target_path(pool):
    """(type, chemin cible) d'un pool de stockage, ou (None, None) si
    illisible -- partage entre la migration (chantier 27) et la HA
    (chantier 17), toutes deux ayant besoin de savoir si un disque vit sur
    du stockage PARTAGE (pool 'netfs', chantier 26)."""
    try:
        root = ET.fromstring(pool.XMLDesc(0))
        return root.get("type"), root.findtext("target/path")
    except (libvirt.libvirtError, ET.ParseError):
        return None, None


def domain_disk_paths(domain):
    """Chemins de tous les disques FICHIER (device='disk', pas les CD-ROM)
    d'un domaine -- ignore delibirement les CD-ROM/ISO, jamais consideres
    comme du stockage 'partage' au sens de ce projet."""
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
    """True seulement si CHAQUE disque de la VM vit sur un pool 'netfs'
    (chantier 26) qui existe ET est actif sur `dest_conn`, SOUS LE MEME NOM
    -- condition suffisante en pratique puisque le chemin de montage local
    d'un pool netfs est toujours derive du nom du pool (voir create_pool,
    app/routers/storage.py), donc un meme nom des deux cotes implique le
    meme export NFS monte au meme endroit. Utilise par la migration a
    chaud (chantier 27, ou 'dest_conn' est le nœud cible reel) ET par la
    HA (chantier 17, ou c'est une condition PREALABLE a la protection --
    une VM dont le disque n'est pas partage ne peut pas etre recuperee
    sur un autre nœud si le nœud source tombe reellement en panne)."""
    disk_paths = domain_disk_paths(domain)
    if not disk_paths:
        return True  # rien a copier/partager (VM sans disque fichier, rare)

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


def ensure_vnc_graphics(conn, domain):
    # S'assure qu'un domaine dispose d'un peripherique graphique VNC.
    # Si absent et que le domaine est arrete, l'ajoute et redefinit le domaine.
    # Renvoie True si une modification a ete faite, False sinon (deja present,
    # ou domaine actif -> impossible a ajouter a chaud de maniere fiable).
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
