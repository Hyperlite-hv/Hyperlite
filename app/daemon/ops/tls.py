"""This node's HTTPS certificate, done as root (docs/design/privilege-separation.md, section 13, host system part 3): the
key and certificate in /etc/hyperlite/local, a self-signed pair, certbot, and the restart of the API that reads them at
start-up. The implementation stays in app/core/tls_certs.py; the routes (app/routers/certificate.py) ask for these."""

import functools
import subprocess

from pydantic import Field

from app.core import tls_certs
from app.daemon.registry import Args, DaemonError, NoArgs, operation

PEM = tls_certs.MAX_PEM  # the API already refuses longer; checked again here


def _refusals(fn):
    """A pair or a request tls_certs refuses (CertError): the daemon's "refused", its message for the user."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        try:
            return fn(args, ctx)
        except tls_certs.CertError as e:
            raise DaemonError("refused", str(e)) from e

    return wrapper


class ImportArgs(Args):
    certificate: str = Field(max_length=PEM)
    key: str = Field(max_length=PEM)
    chain: str = Field(default="", max_length=PEM)


@operation("tls.import", ImportArgs)
@_refusals
def import_pair(args, ctx):
    """Install a certificate and its key (the previous pair kept); checked as uvicorn will load them."""
    return tls_certs.import_pem(args.certificate, args.key, args.chain)


@operation("tls.restore_previous", NoArgs)
@_refusals
def restore_previous(args, ctx):
    tls_certs.restore_previous()
    return None


@operation("tls.self_signed", NoArgs)
def self_signed(args, ctx):
    try:
        tls_certs.self_signed()
    except (subprocess.SubprocessError, OSError) as e:
        raise DaemonError("failed", f"The self-signed certificate could not be created: {e}") from e
    return None


class AcmeArgs(Args):
    domain: str = Field(max_length=253)
    email: str = Field(max_length=254)
    test: bool = False
    timeout: int = Field(default=240, ge=30, le=900)


@operation("tls.acme", AcmeArgs)
@_refusals
def acme(args, ctx):
    """certbot for a Let's Encrypt certificate (HTTP-01); its deploy hook installs the pair and restarts the API. The
    domain and the address are checked again (tls_certs.acme_config). Returns certbot's exit code and last lines."""
    try:
        run = tls_certs.run_acme(args.domain, args.email, args.test, args.timeout)
    except subprocess.TimeoutExpired:
        return {"returncode": None, "tail": "certbot did not finish in time"}
    tail = "\n".join((run.stderr or run.stdout or "").strip().splitlines()[-8:])
    return {"returncode": run.returncode, "tail": tail}


@operation("hyperlite.restart_api", NoArgs)
def restart_api(args, ctx):
    """Restart hyperlite.service in 2 s, out of its cgroup: after a certificate change, which it reads at start-up."""
    from app.routers.update import _spawn_outside_service

    _spawn_outside_service("hyperlite-tls-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])
    return None
