#!/bin/bash
# certbot deploy hook (app/core/tls_certs.py): installs the certificate certbot just issued or renewed as the web
# interface's certificate, then restarts hyperlite.service. certbot runs it after a successful issuance or renewal,
# with RENEWED_LINEAGE pointing at /etc/letsencrypt/live/<domain>.
#
# The pair is checked and installed by the same code as an imported certificate (loaded like uvicorn loads it,
# previous pair kept). The restart goes through a transient systemd timer: when certbot was started by the service
# itself (first issuance from the dashboard), restarting from inside its cgroup would kill certbot mid-hook.
set -euo pipefail

REPO=/root/hyperlite
[ -n "${RENEWED_LINEAGE:-}" ] || { echo "RENEWED_LINEAGE is not set: run by certbot only" >&2; exit 1; }
DOMAIN=$(basename "$RENEWED_LINEAGE")

cd "$REPO"
"$REPO/venv/bin/python" -m app.core.tls_certs "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" "acme:$DOMAIN"

if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --quiet --collect --on-active=3 /bin/systemctl restart hyperlite
else
    (sleep 3 && systemctl restart hyperlite) >/dev/null 2>&1 &
fi
