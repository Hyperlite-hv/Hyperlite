# Architecture

## Overview

```
Browser (React dashboard)
   │  HTTPS (REST + WebSocket)
   ▼
FastAPI service (uvicorn, runs as root)         SQLite (WAL) hyperlite.db
   ├─ app/routers/*   HTTP endpoints            audit_log, tasks, users, ACL, jobs, ...
   ├─ app/core/*      business logic
   │
   ├─ libvirt (qemu:///system)      local VMs
   ├─ libvirt (lxc:///system)       local containers
   ├─ libvirt (qemu+ssh://node/...) remote nodes (dedicated SSH key)
   └─ CLI tools: zpool/zfs, qemu-img, iptables, skopeo/umoci, openssl, ssh/scp
```

There is no agent on the managed nodes: remote hosts are driven through libvirt over SSH.

## Backend (`app/`)

- `app/main.py` creates the FastAPI application, registers the routers, serves the built dashboard (`dashboard/dist`) and starts the background loops (metrics collection, node poller, scheduled backups, inactive-VM cleanup, update check).
- `app/routers/` contains one module per API area (`vms`, `containers`, `storage`, `network`, `nodes`, `ha`, `backups`, `auth`, `sso`, `acl`, `groups`, `pools`, `notifications`, `jobs`, `metrics`, `tasks`, `audit`, `update`, `host`, ...).
- `app/core/` contains the logic shared by the routers: libvirt helpers, VM and container builders, unattended installation, ZFS, cluster and compatibility checks, permissions, security, notifications, backups, HA, metrics, preflight checks and host capability detection.
- `app/core/database.py` creates the schema at start-up (`CREATE TABLE IF NOT EXISTS` plus small in-place migrations). SQLite runs in WAL mode with a 30 second timeout.
- `app/core/audit.py` owns a dedicated writer thread for the audit log so that request handlers never wait on a SQLite write for it.

## Frontend (`dashboard/`)

React 18 with Vite, Tailwind CSS, Zustand (state), react-router and Recharts. The API client is `src/api/client.js`; the global store is `src/store/useInfraStore.js`. The VNC console (noVNC) and terminal (xterm.js) are vendored under `public/` and loaded on demand in a separate browser window.

## Data model

Hyperlite keeps very little state of its own: libvirt is the source of truth for VMs, networks and storage pools. SQLite stores users, permissions, API tokens, tasks, the audit log, backups and schedules, jobs, notification channels, node registrations, HA cache, firewall rules for networks, and small per-VM metadata (OS label, inactivity settings).

## Security model

- Authentication: local accounts (bcrypt), optional TOTP, API tokens (`hlt_` prefix, SHA-256 hash stored), optional OIDC single sign-on. Sessions are HS256 JWTs signed with `HYPERLITE_SECRET_KEY`.
- Authorization: a global role (`admin` or `observateur`) plus scoped rights (roles, groups, pools, per-VM and per-container ACL) evaluated in `app/core/permissions.py`. Endpoints declare the privilege they require.
- Consoles and terminals use short-lived single-use tickets.
- Secrets in the database (SMTP password, OIDC client secret) are encrypted with a Fernet key from `.env`.
- Cluster nodes: dedicated SSH key, trust-on-first-use host keys, and a reverse-trust key per node (restricted with `from=`) for peer-to-peer migration.
- User-supplied URLs are restricted to `http(s)` (`app/core/http_safety.py`).

## Deployment layout

The package installs the application in `/root/hyperlite` and a systemd unit (`hyperlite.service`) serving HTTPS on port 8000 with a self-signed certificate created on first start (`scripts/ensure-tls-cert.sh`). Runtime data lives under `/root/hyperlite/data` (ISOs, templates, SSH keys, TLS, exports).
