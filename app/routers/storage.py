from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import libvirt

from app.core.libvirt_utils import open_conn, ensure_default_pool, get_disk_paths_in_use
from app.core.vm_builder import validate_name
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.error_messages import describe_exception

router = APIRouter(prefix="/storage", tags=["storage"])

POOL_STATE_NAMES = {
    0: "inactif",
    1: "cree",
    2: "en_construction",
    3: "actif",
    4: "suppression",
    5: "inconnu",
}


def _pool_summary(pool):
    state, capacity, allocation, available = pool.info()
    return {
        "nom": pool.name(),
        "uuid": pool.UUIDString(),
        "etat": POOL_STATE_NAMES.get(state, "inconnu"),
        "autostart": bool(pool.autostart()),
        "capacite_go": round(capacity / (1024 ** 3), 2),
        "allocation_go": round(allocation / (1024 ** 3), 2),
        "disponible_go": round(available / (1024 ** 3), 2),
    }


@router.get("")
def list_pools(user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        ensure_default_pool(conn)
        pools = conn.listAllStoragePools()
        result = [_pool_summary(p) for p in pools]
        log_action(user["username"], "list_storage_pools", "storage", "succes")
        return result
    finally:
        conn.close()


@router.get("/{pool_name}/volumes")
def list_volumes(pool_name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
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
