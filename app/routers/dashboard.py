from fastapi import APIRouter, Depends
import libvirt

from app.core.libvirt_utils import open_conn, ensure_default_pool
from app.core.security import get_current_user

router = APIRouter(tags=["dashboard"])


def _get_free_memory_kb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def _get_host_uptime_s():
    # /proc/uptime : "<secondes depuis le boot> <secondes idle cumulees>",
    # le premier nombre est ce qu'on veut.
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


@router.get("/dashboard")
def dashboard(user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        hostname = conn.getHostname()
        hv_type = conn.getType()
        connected = conn.isAlive() == 1

        domains = conn.listAllDomains()
        total = len(domains)
        active = sum(1 for d in domains if d.isActive())
        inactive = total - active

        mem_available_kb = _get_free_memory_kb()

        try:
            pool = ensure_default_pool(conn)
            pool.refresh(0)
            _, capacity, allocation, available = pool.info()
        except libvirt.libvirtError:
            capacity = allocation = available = None

        return {
            "hyperviseur": {
                "nom": hostname,
                "type": hv_type,
                "connecte": connected,
                "uptime_s": _get_host_uptime_s(),
            },
            "vms": {
                "total": total,
                "actives": active,
                "arretees": inactive,
            },
            "memoire_disponible_mo": round(mem_available_kb / 1024, 1) if mem_available_kb else None,
            "stockage": {
                "capacite_go": round(capacity / (1024 ** 3), 2) if capacity else None,
                "disponible_go": round(available / (1024 ** 3), 2) if available else None,
            },
            "etat_infrastructure": "ok" if connected else "degrade",
        }
    finally:
        conn.close()
