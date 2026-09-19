#!/bin/bash
# Starts a throw-away Hyperlite backend for the end-to-end tests: its own SQLite database
# and secrets, HTTP on loopback only. It uses the local libvirt daemon, so run it on a
# development or test machine, never on a production host.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${E2E_WORKDIR:-$(mktemp -d /tmp/hyperlite-e2e.XXXXXX)}"
export HYPERLITE_DB_PATH="$WORK/hyperlite.db"
export HYPERLITE_SECRET_KEY="e2e-$(openssl rand -hex 16)"
export HYPERLITE_ENCRYPTION_KEY="$("${ROOT}/venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export HYPERLITE_INITIAL_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-E2e-Admin-2026}"
cd "$ROOT"
PY="$ROOT/venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${E2E_PORT:-8011}"
