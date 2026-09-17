import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.seed import seed_admin
from app.core.metrics import start_metrics_collector
from app.core.backups import start_backup_scheduler
from app.core.jobs import ensure_lb_job_exists
from app.core.cluster import start_node_poller
from app.core.libvirt_utils import open_conn
from app.core.network_firewall import reapply_all as reapply_network_firewalls
from app.core.vm_cleanup import start_auto_cleanup_scheduler
from app.core.update_check import start_update_check_scheduler
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.vms import router as vms_router
from app.routers.storage import router as storage_router
from app.routers.network import router as network_router
from app.routers.templates import router as templates_router
from app.routers.isos import router as isos_router
from app.routers.audit import router as audit_router
from app.routers.tasks import router as tasks_router
from app.routers.groups import router as groups_router
from app.routers.pools import router as pools_router
from app.routers.acl import router as acl_router
from app.routers.host import router as host_router
from app.routers.update import router as update_router
from app.routers.metrics import router as metrics_router
from app.routers.backups import router as backups_router
from app.routers.jobs import router as jobs_router
from app.routers.nodes import router as nodes_router
from app.routers.containers import router as containers_router
from app.routers.vm_disks import router as vm_disks_router
from app.routers.vm_export import router as vm_export_router
from app.routers.ha import router as ha_router
from app.routers.notifications import router as notifications_router

app = FastAPI(title="Hyperlite API")
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(vms_router)
app.include_router(storage_router)
app.include_router(network_router)
app.include_router(templates_router)
app.include_router(isos_router)
app.include_router(audit_router)
app.include_router(tasks_router)
app.include_router(groups_router)
app.include_router(pools_router)
app.include_router(acl_router)
app.include_router(host_router)
app.include_router(update_router)
app.include_router(metrics_router)
app.include_router(backups_router)
app.include_router(jobs_router)
app.include_router(nodes_router)
app.include_router(containers_router)
app.include_router(vm_disks_router)
app.include_router(vm_export_router)
app.include_router(ha_router)
app.include_router(notifications_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Nouveau front (React/Vite, dashboard/) : devient l'interface servie a la
# racine. L'ancien front vanilla-JS reste accessible a /legacy en secours
# (memes routes API pour les deux, /static/* n'a pas bouge).
DASHBOARD_DIST = "dashboard/dist"
if os.path.isdir(DASHBOARD_DIST):
    app.mount("/assets", StaticFiles(directory=f"{DASHBOARD_DIST}/assets"), name="dashboard-assets")
    app.mount("/novnc", StaticFiles(directory=f"{DASHBOARD_DIST}/novnc"), name="dashboard-novnc")
    app.mount("/xterm", StaticFiles(directory=f"{DASHBOARD_DIST}/xterm"), name="dashboard-xterm")


@app.get("/favicon.svg", include_in_schema=False)
def serve_favicon():
    # Vite copie dashboard/public/favicon.svg a la RACINE de dist/, pas sous
    # /assets -- sans cette route explicite, le catch-all SPA plus bas
    # (serve_ui, qui matche litteralement n'importe quel chemin) interceptait
    # /favicon.svg et renvoyait index.html a la place du vrai fichier :
    # l'onglet du navigateur n'affichait jamais l'icone (constate en testant
    # le nouveau logo Hyperlite).
    favicon_path = f"{DASHBOARD_DIST}/favicon.svg"
    if os.path.isfile(favicon_path):
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"detail": "favicon introuvable"})


@app.get("/health")
def health():
    import platform
    import sys
    import fastapi as _fastapi
    try:
        import uvicorn as _uvicorn
        uvicorn_version = _uvicorn.__version__
    except ImportError:
        uvicorn_version = None

    conn = open_conn()
    try:
        return {
            "status": "ok",
            "hypervisor": conn.getType(),
            "hostname": conn.getHostname(),
            "libvirt_version": conn.getLibVersion(),
            # Introspecte pour de vrai (platform/sys/fastapi/uvicorn) plutot
            # que des chaines figees dans le code -- ce panneau (voir
            # NodeSystemTab.jsx) etait jusqu'ici marque "Mock uniquement"
            # avec un noyau/une version Python codes en dur, jamais mis a
            # jour si l'hote change. Trouve et corrige au chantier 10.
            "kernel": platform.release(),
            "python_version": sys.version.split()[0],
            "fastapi_version": _fastapi.__version__,
            "uvicorn_version": uvicorn_version,
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
    start_metrics_collector()
    start_backup_scheduler()
    ensure_lb_job_exists()
    start_node_poller()
    start_auto_cleanup_scheduler()
    start_update_check_scheduler()

    # Chantier 21 : les regles iptables du pare-feu reseau ne survivent
    # pas a un redemarrage de l'hote (contrairement au nwfilter du
    # pare-feu par VM, gere par libvirt lui-meme) -- reapplique tout ce
    # qui est persiste en base a chaque demarrage du service.
    conn = open_conn()
    try:
        reapply_network_firewalls(conn)
    finally:
        conn.close()
