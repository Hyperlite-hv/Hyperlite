"""Endpoints of the `hyperlite` workstation client: sign-in by device authorization,
downloads of the client itself, and the settings the dashboard shows. The tunnel
itself is in app/routers/vms/tunnel.py; the logic in app/core/workstation.py."""

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.core import workstation
from app.core.audit import log_action
from app.core.security import get_current_user, oauth2_scheme

router = APIRouter(tags=["workstation"])

# Built by installer/build-cli.sh and shipped in the package next to the dashboard.
CLI_DIST = Path(__file__).resolve().parent.parent.parent / "cli" / "dist"
PLATFORMS = {
    "windows-amd64": "hyperlite.exe",
    "windows-arm64": "hyperlite.exe",
    "linux-amd64": "hyperlite",
    "linux-arm64": "hyperlite",
    "darwin-amd64": "hyperlite",
    "darwin-arm64": "hyperlite",
}
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]{1,40}$")
_sha_cache = {}  # path -> (mtime, sha256)


def _binary(platform):
    return CLI_DIST / platform / PLATFORMS[platform]


def _sha256(path):
    mtime = path.stat().st_mtime
    cached = _sha_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _sha_cache[path] = (mtime, digest)
    return digest


def _downloads():
    out = []
    for platform, filename in PLATFORMS.items():
        path = _binary(platform)
        if path.is_file():
            out.append(
                {"platform": platform, "filename": filename, "size": path.stat().st_size, "sha256": _sha256(path)}
            )
    return out


@router.get("/workstation/config")
def workstation_config(user: dict = Depends(get_current_user)):
    return {
        "tunnel_ports": workstation.tunnel_ports(),
        "tunnel_idle_timeout_s": workstation.tunnel_idle_timeout_s(),
        "cli_token_days": workstation.cli_token_days(),
        "downloads": _downloads(),
    }


@router.get("/downloads/hyperlite/{platform}", include_in_schema=False)
def download_client(platform: str):
    """Public on purpose, like any installer: the binary holds no secret, and a
    workstation without a session must be able to fetch it (or a deployment tool)."""
    if platform not in PLATFORMS or not _binary(platform).is_file():
        raise HTTPException(status_code=404, detail="Client not available for this platform on this server")
    return FileResponse(_binary(platform), media_type="application/octet-stream", filename=PLATFORMS[platform])


# ---- Device authorization ----


def _client_ip(request: Request):
    return request.client.host if request.client else "unknown"


@router.post("/auth/cli/start")
def cli_start(request: Request, hostname: str = Body(..., embed=True), client_version: str = Body("", embed=True)):
    hostname = hostname.strip()
    if not HOSTNAME_RE.match(hostname):
        raise HTTPException(status_code=422, detail="Invalid workstation name")
    if client_version and not VERSION_RE.match(client_version):
        raise HTTPException(status_code=422, detail="Invalid client version")
    started = workstation.start_request(hostname, client_version, _client_ip(request))
    if started is None:
        raise HTTPException(
            status_code=429, detail="Too many sign-in requests in progress: try again in a few minutes."
        )
    device_code, user_code = started
    base = str(request.base_url).rstrip("/")
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": f"{base}/cli-login",
        "verification_uri_complete": f"{base}/cli-login?code={user_code}",
        "expires_in": workstation.CLI_REQUEST_TTL,
        "interval": workstation.CLI_POLL_INTERVAL_S,
    }


@router.post("/auth/cli/token")
def cli_token(device_code: str = Body(..., embed=True)):
    """Polled by the client. The error codes are those of RFC 8628."""
    status, info = workstation.poll_request(device_code)
    if status != "approved":
        code = {"pending": "authorization_pending", "denied": "access_denied"}.get(status, "expired_token")
        return JSONResponse(status_code=400, content={"error": code})
    log_action(info["username"], "cli_login", info["hostname"], "succes")
    return {
        "access_token": info["token"],
        "token_id": info["token_id"],
        "token_type": "Bearer",
        "username": info["username"],
        "expires_at": info["expires_at"],
    }


async def _session_user(token: str = Depends(oauth2_scheme)):
    """Approving a workstation needs a web session: an API token (for example a
    workstation's own) cannot approve another workstation."""
    if token.startswith("hlt_"):
        raise HTTPException(status_code=403, detail="Approve the workstation from the web interface.")
    return await get_current_user(token)


@router.get("/auth/cli/requests/{user_code}")
def cli_request(user_code: str, user: dict = Depends(_session_user)):
    req = workstation.find_request(user_code)
    if req is None or req["status"] != "pending":
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    return {
        "user_code": user_code.upper(),
        "hostname": req["hostname"],
        "client_version": req["client_version"],
        "source_ip": req["source_ip"],
        "created_at": req["created_at"],
        "token_days": workstation.cli_token_days(),
    }


@router.post("/auth/cli/requests/{user_code}/approve")
def cli_approve(user_code: str, user: dict = Depends(_session_user)):
    req = workstation.decide_request(user_code, user["username"], approve=True)
    if req is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    log_action(user["username"], "cli_approve", req["hostname"], "succes")
    return {"ok": True, "hostname": req["hostname"]}


@router.post("/auth/cli/requests/{user_code}/deny")
def cli_deny(user_code: str, user: dict = Depends(_session_user)):
    req = workstation.decide_request(user_code, user["username"], approve=False)
    if req is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    log_action(user["username"], "cli_deny", req["hostname"], "succes")
    return {"ok": True}
