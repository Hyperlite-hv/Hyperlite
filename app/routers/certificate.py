"""This node's HTTPS certificate (app/core/tls_certs.py): read it, import one, get one from Let's Encrypt, or go
back to the previous or a self-signed one. Administrators only: the key protects every session and API token.

The service reads the certificate at start-up, so every change ends with a restart of hyperlite.service a couple of
seconds after the answer (the dashboard reconnects on its own). What needs root (the key in /etc/hyperlite/local,
certbot, the restart) is done by hyperlite-daemon (app/daemon/ops/tls.py, docs/design/privilege-separation.md,
section 13); these routes keep who may ask, the audit log and the answers."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import privileged, tls_certs
from app.core.audit import log_action
from app.core.security import require_role
from app.daemon.registry import DaemonError

router = APIRouter(prefix="/host/certificate", tags=["host"])

ACME_TIMEOUT_S = 240


def _restart_soon():
    privileged.run("hyperlite.restart_api")


def _ask(user, action, op, target="tls", **args):
    """hyperlite-daemon's answer to `op`. A pair or request it refuses: 422 with the reason, as before; a failure:
    500; without the daemon: 503."""
    try:
        return privileged.run(op, user=user["username"], **args)
    except DaemonError as e:
        log_action(user["username"], action, target, "failed", e.detail)
        status = {"refused": 422, "unavailable": 503}.get(e.kind, 500)
        raise HTTPException(status_code=status, detail=e.detail) from None


@router.get("")
def get_certificate(user: dict = Depends(require_role("admin"))):
    return tls_certs.info()


class CertificateImport(BaseModel):
    certificate: str
    key: str
    chain: str = ""


@router.post("")
def import_certificate(payload: CertificateImport, user: dict = Depends(require_role("admin"))):
    described = _ask(
        user, "import_certificate", "tls.import", certificate=payload.certificate, key=payload.key, chain=payload.chain
    )
    log_action(
        user["username"], "import_certificate", "tls", "success", f"{described['subject']} until {described['end']}"
    )
    _restart_soon()
    return {**tls_certs.info(), "restart": True}


@router.post("/previous")
def restore_previous_certificate(user: dict = Depends(require_role("admin"))):
    _ask(user, "restore_certificate", "tls.restore_previous")
    log_action(user["username"], "restore_certificate", "tls", "success")
    _restart_soon()
    return {**tls_certs.info(), "restart": True}


@router.post("/self-signed")
def self_signed_certificate(user: dict = Depends(require_role("admin"))):
    _ask(user, "self_signed_certificate", "tls.self_signed")
    log_action(user["username"], "self_signed_certificate", "tls", "success")
    _restart_soon()
    return {**tls_certs.info(), "restart": True}


class AcmeRequest(BaseModel):
    domain: str
    email: str
    test: bool = False


@router.post("/acme")
def acme_certificate(payload: AcmeRequest, user: dict = Depends(require_role("admin"))):
    """Let's Encrypt by HTTP-01: the domain must resolve to this node and port 80 must reach it from the Internet.
    certbot's deploy hook installs the certificate and restarts the service; certbot's own timer renews it."""
    run = _ask(
        user,
        "acme_certificate",
        "tls.acme",
        target=payload.domain[:253],
        timeout=ACME_TIMEOUT_S + 30,
        domain=payload.domain,
        email=payload.email,
        test=payload.test,
    )
    if run["returncode"] is None:
        log_action(user["username"], "acme_certificate", payload.domain, "failed", "timeout")
        raise HTTPException(status_code=504, detail="certbot did not finish in time")
    if run["returncode"] != 0:
        # certbot's last lines name the cause (DNS, port 80 unreachable, rate limit): shown as they are.
        log_action(user["username"], "acme_certificate", payload.domain, "failed", run["tail"][-500:])
        raise HTTPException(status_code=502, detail=f"certbot failed:\n{run['tail']}")
    log_action(user["username"], "acme_certificate", payload.domain, "success", "staging" if payload.test else None)
    return {**tls_certs.info(), "restart": True}
