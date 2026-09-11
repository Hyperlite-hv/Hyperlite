from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.seed import seed_admin
from app.core.libvirt_utils import open_conn
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.vms import router as vms_router
from app.routers.storage import router as storage_router
from app.routers.network import router as network_router
from app.routers.templates import router as templates_router
from app.routers.isos import router as isos_router

app = FastAPI(title="Hyperlite API")
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(vms_router)
app.include_router(storage_router)
app.include_router(network_router)
app.include_router(templates_router)
app.include_router(isos_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse("app/static/index.html")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    print(f"ERREUR NON GEREE sur {request.method} {request.url.path} : {exc!r}", flush=True)
    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur"})


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
