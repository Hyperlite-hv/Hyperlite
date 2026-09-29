"""This node's HTTPS certificate (app/core/tls_certs.py): read it, import one, get one from Let's Encrypt, or go
back to the previous or a self-signed one. Administrators only: the key protects every session and API token.

The service reads the certificate at start-up, so every change ends with a restart of hyperlite.service a couple of
seconds after the answer (the dashboard reconnects on its own)."""

import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import tls_certs
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/host/certificate", tags=["host"])

ACME_TIMEOUT_S = 240


def _restart_soon():
    # Imported here: the update router owns the helper that runs a command outside the service's cgroup.
    from app.routers.update import _spawn_outside_service

    _spawn_outside_service("hyperlite-tls-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])


@router.get("")
def get_certificate(user: dict = Depends(require_role("admin"))):
    return tls_certs.info()


class CertificateImport(BaseModel):
    certificat: str
    cle: str
    chaine: str = ""


@router.post("")
def import_certificate(payload: CertificateImport, user: dict = Depends(require_role("admin"))):
    try:
        described = tls_certs.import_pem(payload.certificat, payload.cle, payload.chaine)
    except tls_certs.CertError as e:
        log_action(user["username"], "import_certificate", "tls", "echec", str(e))
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(
        user["username"], "import_certificate", "tls", "succes", f"{described['sujet']} until {described['fin']}"
    )
    _restart_soon()
    return {**tls_certs.info(), "redemarrage": True}


@router.post("/previous")
def restore_previous_certificate(user: dict = Depends(require_role("admin"))):
    try:
        tls_certs.restore_previous()
    except tls_certs.CertError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "restore_certificate", "tls", "succes")
    _restart_soon()
    return {**tls_certs.info(), "redemarrage": True}


@router.post("/self-signed")
def self_signed_certificate(user: dict = Depends(require_role("admin"))):
    try:
        tls_certs.self_signed()
    except (subprocess.SubprocessError, OSError) as e:
        log_action(user["username"], "self_signed_certificate", "tls", "echec", str(e))
        raise HTTPException(status_code=500, detail=f"The self-signed certificate could not be created: {e}") from e
    log_action(user["username"], "self_signed_certificate", "tls", "succes")
    _restart_soon()
    return {**tls_certs.info(), "redemarrage": True}


class AcmeRequest(BaseModel):
    domaine: str
    email: str
    test: bool = False


@router.post("/acme")
def acme_certificate(payload: AcmeRequest, user: dict = Depends(require_role("admin"))):
    """Let's Encrypt by HTTP-01: the domain must resolve to this node and port 80 must reach it from the Internet.
    certbot's deploy hook installs the certificate and restarts the service; certbot's own timer renews it."""
    try:
        cmd = tls_certs.acme_command(payload.domaine, payload.email, payload.test)
    except tls_certs.CertError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=ACME_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log_action(user["username"], "acme_certificate", payload.domaine, "echec", "timeout")
        raise HTTPException(status_code=504, detail="certbot did not finish in time") from None
    if run.returncode != 0:
        # certbot's last lines name the cause (DNS, port 80 unreachable, rate limit): shown as they are.
        tail = "\n".join((run.stderr or run.stdout).strip().splitlines()[-8:])
        log_action(user["username"], "acme_certificate", payload.domaine, "echec", tail[-500:])
        raise HTTPException(status_code=502, detail=f"certbot failed:\n{tail}")
    log_action(user["username"], "acme_certificate", payload.domaine, "succes", "staging" if payload.test else None)
    return {**tls_certs.info(), "redemarrage": True}
