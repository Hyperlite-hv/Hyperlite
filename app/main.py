from fastapi import FastAPI

from app.core.seed import seed_admin
from app.core.libvirt_utils import open_conn
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.vms import router as vms_router
from app.routers.storage import router as storage_router

app = FastAPI(title="Hyperlite API")
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(vms_router)
app.include_router(storage_router)


@app.on_event("startup")
def on_startup():
    pwd = seed_admin()
    if pwd:
        print(f"=== Compte admin cree : admin / {pwd} (notez ce mot de passe) ===", flush=True)


@app.get("/health")
def health():
    conn = open_conn()
    try:
        return {
            "status": "ok",
            "hypervisor": conn.getType(),
            "hostname": conn.getHostname(),
            "libvirt_version": conn.getLibVersion(),
        }
    finally:
        conn.close()
