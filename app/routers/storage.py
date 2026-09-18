import re
import socket
import xml.etree.ElementTree as ET
from xml.sax import saxutils

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import libvirt

from app.core.libvirt_utils import open_conn, ensure_default_pool, get_disk_paths_in_use
from app.core.vm_builder import validate_name
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core import zfs_storage

router = APIRouter(prefix="/storage", tags=["storage"])

# BUG REEL trouve le 2026-09-17 en testant la creation d'un pool NFS (tous
# les pools, y compris "default" deja actif depuis des jours, s'affichaient
# "en_construction") : cette table ne correspondait PAS a l'enumeration
# reelle de libvirt (virStoragePoolState -- verifie via
# libvirt.VIR_STORAGE_POOL_*, seulement 5 valeurs 0-4, pas 6). Sans impact
# visible avant aujourd'hui car aucun ecran n'affichait encore ce champ
# "etat" -- corrige avant de l'exposer dans l'UI de gestion des pools.
POOL_STATE_NAMES = {
    0: "inactif",       # VIR_STORAGE_POOL_INACTIVE
    1: "en_construction",  # VIR_STORAGE_POOL_BUILDING
    2: "actif",          # VIR_STORAGE_POOL_RUNNING
    3: "degrade",         # VIR_STORAGE_POOL_DEGRADED
    4: "inaccessible",    # VIR_STORAGE_POOL_INACCESSIBLE
}

# Nom d'hote/IP (chantier 26, pool NFS) : lettres/chiffres/points/tirets --
# suffisant pour un hostname ou une IPv4/IPv6 simple, exclut tout caractere
# qui pourrait avoir un sens special ailleurs.
NFS_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.:_-]{0,253})$")
# Chemin absolu (export NFS cote serveur, ou repertoire local d'un pool
# "dir") : pas d'espace ni de caracteres XML/shell speciaux.
POOL_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,255}$")


def _pool_type(pool):
    try:
        root = ET.fromstring(pool.XMLDesc(0))
        return root.get("type", "inconnu")
    except (libvirt.libvirtError, ET.ParseError):
        return "inconnu"


def _pool_summary(pool):
    state, capacity, allocation, available = pool.info()
    return {
        "nom": pool.name(),
        "uuid": pool.UUIDString(),
        "type": _pool_type(pool),
        "etat": POOL_STATE_NAMES.get(state, "inconnu"),
        "autostart": bool(pool.autostart()),
        "capacite_go": round(capacity / (1024 ** 3), 2),
        "allocation_go": round(allocation / (1024 ** 3), 2),
        "disponible_go": round(available / (1024 ** 3), 2),
    }


@router.get("")
def list_pools(node: str | None = None, user: dict = Depends(get_current_user)):
    """node : meme convention que GET /vms (chantier 15) -- liste les pools
    d'un noeud distant enregistre plutot que de l'hote local.

    Pools ZFS (backlog stockage 2026-09-18) fusionnes dans la meme liste
    (type='zfs') pour un affichage unifie cote UI -- mais uniquement
    quand `node` designe l'hote LOCAL : contrairement aux pools libvirt
    (dir/netfs), la gestion ZFS de ce chantier est encore mono-noeud
    (appels `zpool`/`zfs` directs sur CE serveur, pas de gestion a
    distance via SSH -- voir app/core/zfs_storage.py). Un pool ZFS d'un
    noeud distant enregistre n'est donc pas visible ici pour l'instant."""
    conn = open_conn(node)
    try:
        ensure_default_pool(conn)
        pools = conn.listAllStoragePools()
        result = [_pool_summary(p) for p in pools]
        if not node or node == "local":
            result += zfs_storage.list_pools()
        log_action(user["username"], "list_storage_pools", "storage", "succes")
        return result
    finally:
        conn.close()


class PoolCreate(BaseModel):
    name: str
    # "dir" : repertoire local au nœud (comme le pool "default" existant).
    # "netfs" : export NFS distant monte par libvirt (chantier 26, stockage
    # partage) -- fondation du chantier 27 (migration a chaud), un disque
    # sur un pool netfs est visible identiquement depuis n'importe quel
    # nœud qui monte le meme export, pas besoin de le copier au moment de
    # migrer une VM.
    # "zfs" (backlog stockage 2026-09-18) : pool ZFS gere hors libvirt (voir
    # app/core/zfs_storage.py), adosse pour l'instant a un fichier loopback
    # (`size_gb`) plutot qu'un disque dedie -- choix explicite d'Antho pour
    # valider le mecanisme sans toucher au LVM existant d'un nœud.
    type: str = Field(pattern="^(dir|netfs|zfs)$")
    path: str | None = None  # pool "dir" : repertoire local (defaut si omis)
    nfs_host: str | None = None  # pool "netfs" : hote du serveur NFS
    nfs_export_path: str | None = None  # pool "netfs" : chemin exporte cote serveur
    size_gb: int | None = Field(None, ge=1, le=4096)  # pool "zfs" : taille du fichier loopback


def _build_pool_xml(payload: PoolCreate, target_path: str) -> str:
    """Construit le XML libvirt via ElementTree (echappement automatique)
    plutot que par concatenation de chaines -- l'audit securite (chantier
    11) avait justement trouve une injection XML dans la creation reseau
    en mode pont pour cette raison, pas question de repeter l'erreur ici."""
    pool_el = ET.Element("pool", type=payload.type)
    ET.SubElement(pool_el, "name").text = payload.name
    if payload.type == "netfs":
        source_el = ET.SubElement(pool_el, "source")
        ET.SubElement(source_el, "host", name=payload.nfs_host)
        ET.SubElement(source_el, "dir", path=payload.nfs_export_path)
        ET.SubElement(source_el, "format", type="nfs")
        # BUG REEL trouve en testant un partage NFS reellement inter-
        # machines (chantier 17, HA) : sur un client NFS Debian 13/trixie
        # (nfs-utils + noyau recents, verifie sur serveur-antho), le
        # montage echoue systematiquement avec "NFS: mount program didn't
        # pass remote address" -- un mount(8) manuel SANS l'option 'addr='
        # explicite echoue de la meme facon, AVEC elle il reussit. Semble
        # etre une regression du chemin de montage recent (fsconfig/nouvelle
        # API de montage du noyau) qui ne deduit plus l'adresse depuis le
        # nom d'hote fourni. Ajoutee systematiquement -- inoffensive sur un
        # client NFS plus ancien qui n'en a pas besoin. 'addr' veut une IP,
        # pas un nom d'hote -- gethostbyname() sur une IP litterale la
        # renvoie telle quelle (no-op), pas besoin de detecter le cas au
        # prealable.
        #
        # DEUXIEME bug trouve dans la foulee (meme test reel, client NFS
        # Debian 13/trixie) : meme avec 'addr=' correctement transmis,
        # le montage echoue ensuite avec "NFS: Version unavailable" tant
        # que la version NFS n'est pas fixee explicitement -- la
        # negociation automatique echoue silencieusement sur ce client.
        # 'vers=4.2' ajoute pour la meme raison.
        #
        # L'element s'appelle 'mount_opts' (PAS 'mountopts', erreur faite
        # une premiere fois -- silencieusement ignore par libvirt sans
        # rien dans l'erreur pour l'indiquer) et vit dans son PROPRE espace
        # de noms XML (verifie dans /usr/share/libvirt/schemas/
        # storagepool.rng sur cette machine, pas dans la documentation en
        # ligne). TROISIEME piege trouve en testant : construit via
        # ET.SubElement avec un tag qualifie '{namespace}mount_opts',
        # ET.tostring() serialise ca en declarant le namespace comme un
        # PREFIXE sur la racine <pool xmlns:ns0="..."> puis <ns0:mount_opts>
        # -- syntaxiquement correct, mais libvirt sur cette version
        # (Debian 13/serveur-antho) l'ignore silencieusement quand meme
        # (verifie : l'element est absent du XML RELU juste apres
        # defineXML). Seule la forme "xmlns=... sur l'element lui-meme"
        # (namespace par defaut LOCAL, pas un prefixe racine) est
        # effectivement prise en compte -- ET ne genere jamais cette forme
        # precise. Le reste du document reste construit via ElementTree
        # (echappement automatique, voir plus haut) ; seul ce fragment est
        # assemble comme chaine, avec des valeurs deja validees (regex
        # NFS_HOST_RE plus haut) ou resolues via gethostbyname -- jamais du
        # texte utilisateur brut.
        try:
            addr = socket.gethostbyname(payload.nfs_host)
        except OSError:
            addr = payload.nfs_host  # echec de resolution : tente quand meme avec la valeur fournie telle quelle
        mount_opts_xml = (
            f'<mount_opts xmlns="http://libvirt.org/schemas/storagepool/fs/1.0">'
            f'<option name="addr={saxutils.escape(addr)}"/><option name="vers=4.2"/></mount_opts>'
        )
    else:
        mount_opts_xml = ""
    target_el = ET.SubElement(pool_el, "target")
    ET.SubElement(target_el, "path").text = target_path
    pool_xml = ET.tostring(pool_el, encoding="unicode")
    if mount_opts_xml:
        pool_xml = pool_xml.replace("</source>", "</source>" + mount_opts_xml, 1)
    return pool_xml


@router.post("", status_code=201)
def create_pool(payload: PoolCreate, node: str | None = None, user: dict = Depends(require_role("admin"))):
    name_error = validate_name(payload.name)
    if name_error:
        log_action(user["username"], "create_storage_pool", payload.name, "echec", name_error)
        raise HTTPException(status_code=422, detail=name_error)

    if payload.type == "zfs":
        if node and node != "local":
            raise HTTPException(status_code=422, detail="Un pool ZFS ne peut être créé que sur l'hôte local (gestion mono-nœud pour l'instant)")
        if not payload.size_gb:
            raise HTTPException(status_code=422, detail="size_gb est requis pour un pool ZFS")
        if not zfs_storage.is_available():
            raise HTTPException(status_code=422, detail="ZFS n'est pas installé sur cet hôte (paquets zfsutils-linux/zfs-dkms)")
        try:
            pool = zfs_storage.create_pool(payload.name, payload.size_gb)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "create_storage_pool", payload.name, "echec", e.message)
            raise HTTPException(status_code=500, detail=f"Erreur de création du pool ZFS : {e.message}")
        log_action(user["username"], "create_storage_pool", payload.name, "succes")
        return pool

    if payload.type == "dir":
        target_path = payload.path or f"/var/lib/libvirt/hyperlite-pools/{payload.name}"
        if not POOL_PATH_RE.match(target_path):
            raise HTTPException(status_code=422, detail="Chemin de pool invalide (doit être un chemin absolu, sans espace ni caractère spécial)")
    else:
        if not payload.nfs_host or not payload.nfs_export_path:
            raise HTTPException(status_code=422, detail="nfs_host et nfs_export_path sont requis pour un pool NFS")
        if not NFS_HOST_RE.match(payload.nfs_host):
            raise HTTPException(status_code=422, detail="Hôte NFS invalide")
        if not POOL_PATH_RE.match(payload.nfs_export_path):
            raise HTTPException(status_code=422, detail="Chemin d'export NFS invalide (doit être un chemin absolu)")
        # Point de montage LOCAL au nœud (cote client NFS) -- distinct du
        # chemin exporte cote serveur, jamais fourni par l'appelant pour
        # eviter toute collision avec un repertoire systeme existant.
        target_path = f"/var/lib/libvirt/hyperlite-pools/{payload.name}"

    conn = open_conn(node)
    try:
        try:
            conn.storagePoolLookupByName(payload.name)
            log_action(user["username"], "create_storage_pool", payload.name, "echec", "Pool déjà existant")
            raise HTTPException(status_code=422, detail=f"Un pool '{payload.name}' existe déjà")
        except libvirt.libvirtError:
            pass

        pool_xml = _build_pool_xml(payload, target_path)
        try:
            pool = conn.storagePoolDefineXML(pool_xml)
            # build() cree le repertoire local ("dir") ou le point de montage
            # ("netfs") -- necessaire avant create() sur un pool tout neuf.
            # flags=0 : pas de reformatage destructif d'un support existant.
            pool.build(0)
            pool.create(0)
            pool.setAutostart(True)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "create_storage_pool", payload.name, "echec", msg)
            # Nettoyage best-effort si la definition a reussi mais pas le
            # demarrage (ex. export NFS injoignable) -- evite un pool
            # "fantome" defini mais jamais utilisable qui bloquerait un
            # nouvel essai avec le meme nom.
            try:
                conn.storagePoolLookupByName(payload.name).undefine()
            except libvirt.libvirtError:
                pass
            raise HTTPException(status_code=500, detail=f"Erreur de création du pool : {msg}")

        log_action(user["username"], "create_storage_pool", payload.name, "succes")
        return _pool_summary(pool)
    finally:
        conn.close()


@router.delete("/{pool_name}")
def delete_pool(pool_name: str, node: str | None = None, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if pool_name == "default":
        raise HTTPException(status_code=400, detail="Le pool 'default' ne peut pas être supprimé")
    if not confirm:
        raise HTTPException(status_code=400, detail="Action irréversible : ajoutez ?confirm=true pour confirmer la suppression")

    # Un pool ZFS n'est PAS un pool libvirt (voir zfs_storage.py) -- routé
    # à part avant toute tentative de lookup côté libvirt, qui échouerait
    # simplement avec "introuvable" pour un nom qui n'existe que côté ZFS.
    if (not node or node == "local") and zfs_storage.pool_exists(pool_name):
        try:
            zfs_storage.delete_pool(pool_name)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "delete_storage_pool", pool_name, "echec", e.message)
            raise HTTPException(status_code=409, detail=e.message)
        log_action(user["username"], "delete_storage_pool", pool_name, "succes")
        return {"message": f"Pool ZFS '{pool_name}' supprimé"}

    conn = open_conn(node)
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Pool de stockage '{pool_name}' introuvable")

        pool.refresh(0)
        if pool.listAllVolumes():
            log_action(user["username"], "delete_storage_pool", pool_name, "echec", "Pool non vide")
            raise HTTPException(status_code=409, detail=f"Le pool '{pool_name}' contient encore des volumes, supprimez-les d'abord")

        try:
            if pool.isActive():
                # netfs : demonte l'export. dir : ne touche pas au contenu du
                # repertoire (deja verifie vide ci-dessus).
                pool.destroy()
            pool.undefine()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_storage_pool", pool_name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Erreur de suppression du pool : {msg}")

        log_action(user["username"], "delete_storage_pool", pool_name, "succes")
        return {"message": f"Pool '{pool_name}' supprimé"}
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
            raise HTTPException(status_code=404, detail=f"Pool de stockage '{pool_name}' introuvable")
        pool.refresh(0)
        in_use = get_disk_paths_in_use(conn)
        result = []
        for vol in pool.listAllVolumes():
            vol_info = vol.info()
            result.append({
                "nom": vol.name(),
                "chemin": vol.path(),
                "capacite_go": round(vol_info[1] / (1024 ** 3), 3),
                "allocation_go": round(vol_info[2] / (1024 ** 3), 3),
                "utilise": vol.path() in in_use,
            })
        log_action(user["username"], "list_volumes", pool_name, "succes")
        return result
    finally:
        conn.close()


class VolumeCreate(BaseModel):
    name: str
    size_gb: int = Field(ge=1, le=100)


@router.post("/{pool_name}/volumes", status_code=201)
def create_volume(pool_name: str, payload: VolumeCreate, user: dict = Depends(require_role("admin"))):
    if zfs_storage.pool_exists(pool_name):
        name_error = zfs_storage.validate_zfs_name(payload.name)
        if name_error:
            log_action(user["username"], "create_volume", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)
        try:
            path = zfs_storage.create_zvol(pool_name, payload.name, payload.size_gb)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "create_volume", payload.name, "echec", e.message)
            raise HTTPException(status_code=500, detail=f"Erreur de création du zvol : {e.message}")
        log_action(user["username"], "create_volume", payload.name, "succes")
        return {"nom": payload.name, "chemin": path, "capacite_go": float(payload.size_gb)}

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_volume", payload.name, "echec", "Pool introuvable")
            raise HTTPException(status_code=404, detail=f"Pool de stockage '{pool_name}' introuvable")

        base_name = payload.name[:-len(".qcow2")] if payload.name.endswith(".qcow2") else payload.name
        name_error = validate_name(base_name)
        if name_error:
            log_action(user["username"], "create_volume", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)
        filename = f"{base_name}.qcow2"
        try:
            pool.storageVolLookupByName(filename)
            log_action(user["username"], "create_volume", filename, "echec", "Volume déjà existant")
            raise HTTPException(status_code=422, detail=f"Un volume '{filename}' existe déjà dans ce pool")
        except libvirt.libvirtError:
            pass

        size_bytes = payload.size_gb * (1024 ** 3)
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
            raise HTTPException(status_code=500, detail=f"Erreur de création du volume : {msg}")

        log_action(user["username"], "create_volume", filename, "succes")
        vol_info = vol.info()
        return {
            "nom": vol.name(),
            "chemin": vol.path(),
            "capacite_go": round(vol_info[1] / (1024 ** 3), 3),
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
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume utilisé par une VM")
            raise HTTPException(status_code=409, detail=f"Le volume '{volume_name}' est utilisé par une VM, suppression refusée")
        if not confirm:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Action irréversible : ajoutez ?confirm=true pour confirmer la suppression")
        try:
            zfs_storage.delete_zvol(pool_name, volume_name)
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "delete_volume", volume_name, "echec", e.message)
            raise HTTPException(status_code=404 if "introuvable" in e.message else 500, detail=e.message)
        log_action(user["username"], "delete_volume", volume_name, "succes")
        return {"message": f"Volume '{volume_name}' supprimé"}

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Pool de stockage '{pool_name}' introuvable")
        try:
            vol = pool.storageVolLookupByName(volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume introuvable")
            raise HTTPException(status_code=404, detail=f"Volume '{volume_name}' introuvable")

        in_use = get_disk_paths_in_use(conn)
        if vol.path() in in_use:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Volume utilisé par une VM")
            raise HTTPException(status_code=409, detail=f"Le volume '{volume_name}' est utilisé par une VM, suppression refusée")

        if not confirm:
            log_action(user["username"], "delete_volume", volume_name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Action irréversible : ajoutez ?confirm=true pour confirmer la suppression")

        try:
            vol.delete(0)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_volume", volume_name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Erreur de suppression : {msg}")

        log_action(user["username"], "delete_volume", volume_name, "succes")
        return {"message": f"Volume '{volume_name}' supprimé"}
    finally:
        conn.close()
