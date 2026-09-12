import os

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
from app.routers.audit import router as audit_router

app = FastAPI(title="Hyperlite API")
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(vms_router)
app.include_router(storage_router)
app.include_router(network_router)
app.include_router(templates_router)
app.include_router(isos_router)
app.include_router(audit_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Nouveau front (React/Vite, dashboard/) : devient l'interface servie a la
# racine. L'ancien front vanilla-JS reste accessible a /legacy en secours
# (memes routes API pour les deux, /static/* n'a pas bouge).
DASHBOARD_DIST = "dashboard/dist"
if os.path.isdir(DASHBOARD_DIST):
    app.mount("/assets", StaticFiles(directory=f"{DASHBOARD_DIST}/assets"), name="dashboard-assets")
    app.mount("/novnc", StaticFiles(directory=f"{DASHBOARD_DIST}/novnc"), name="dashboard-novnc")
    app.mount("/xterm", StaticFiles(directory=f"{DASHBOARD_DIST}/xterm"), name="dashboard-xterm")


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


# index.html reference les fichiers d'assets par leur hash de build (ex.
# index-abc123.js) : sans en-tete explicite, un navigateur peut mettre en
# cache l'index.html lui-meme au-dela d'un rechargement simple et continuer a
# demander un vieux couple JS/CSS apres un nouveau déploiement, ce qui a fait
# croire a un bug visuel deja corrige. "no-cache" force une revalidation a
# chaque chargement (pas un "ne jamais stocker") sans re-télécharger si rien
# n'a change cote serveur.
NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}


@app.get("/legacy", include_in_schema=False)
@app.get("/legacy/{path:path}", include_in_schema=False)
def serve_legacy_ui(path: str = ""):
    return FileResponse("app/static/index.html", headers=NO_CACHE_HEADERS)


@app.get("/", include_in_schema=False)
@app.get("/{path:path}", include_in_schema=False)
def serve_ui(path: str = ""):
    # Catch-all SPA : toute URL cote client (react-router) renvoie index.html,
    # le routing se fait dans le navigateur. Doit rester la DERNIERE route
    # declaree pour ne jamais intercepter les vraies routes API/WebSocket
    # enregistrees au-dessus (elles sont essayees en premier).
    dashboard_index = f"{DASHBOARD_DIST}/index.html"
    if os.path.isfile(dashboard_index):
        return FileResponse(dashboard_index, headers=NO_CACHE_HEADERS)
    return FileResponse("app/static/index.html", headers=NO_CACHE_HEADERS)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    print(f"ERREUR NON GEREE sur {request.method} {request.url.path} : {exc!r}", flush=True)
    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur"})


@app.on_event("startup")
def on_startup():
    pwd = seed_admin()
    if pwd:
        print(f"=== Compte admin cree : admin / {pwd} (notez ce mot de passe) ===", flush=True)
