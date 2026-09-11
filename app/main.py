import libvirt
from fastapi import FastAPI, HTTPException, Depends

from app.core.seed import seed_admin
from app.core.security import get_current_user
from app.core.audit import log_action
from app.routers.auth import router as auth_router

app = FastAPI(title="Hyperlite API")
app.include_router(auth_router)

LIBVIRT_URI = "qemu:///system"


@app.on_event("startup")
def on_startup():
    pwd = seed_admin()
    if pwd:
        print(f"=== Compte admin cree : admin / {pwd} (notez ce mot de passe) ===", flush=True)


def get_conn_libvirt():
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise HTTPException(status_code=500, detail="Connexion libvirt impossible")
    return conn


@app.get("/health")
def health():
    conn = get_conn_libvirt()
    info = {
        "hypervisor": conn.getType(),
        "hostname": conn.getHostname(),
        "libvirt_version": conn.getLibVersion(),
    }
    conn.close()
    return {"status": "ok", **info}


@app.get("/vms")
def list_vms(user: dict = Depends(get_current_user)):
    conn = get_conn_libvirt()
    domains = conn.listAllDomains()
    result = [
        {"name": d.name(), "id": d.ID(), "state": d.state()[0]}
        for d in domains
    ]
    conn.close()
    log_action(user["username"], "list_vms", "vms", "succes")
    return result
