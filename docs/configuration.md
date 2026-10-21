# Configuration

Hyperlite is configured through environment variables, normally set in `/etc/hyperlite/priv/env` (read by systemd through `EnvironmentFile`; `/root/hyperlite/.env` up to 1.0). The package creates this file on first installation with fresh secrets and never overwrites it on upgrade.

| Variable | Purpose | Default |
|---|---|---|
| `HYPERLITE_SECRET_KEY` | Secret the members of a cluster share to sign the calls between them (sessions are signed by hyperlite-daemon from 1.1.1). Must be long and random: the service refuses to start when it is set to fewer than 32 characters (`openssl rand -hex 32`). | random per process (sessions are lost on restart) |
| `HYPERLITE_INITIAL_ADMIN_PASSWORD` | Password of the `admin` account created on first start. Ignored if an admin already exists. | none |
| `HYPERLITE_ENCRYPTION_KEY` | Fernet key used to encrypt secrets stored in the database. Generated automatically into `/etc/hyperlite/priv/env` when missing. | generated |
| `HYPERLITE_VM_MAX_VCPU`, `HYPERLITE_VM_MAX_MEMORY_MB`, `HYPERLITE_VM_MAX_DISK_GB`, `HYPERLITE_VM_MAX_DISKS` | Optional caps on a VM's vCPU, memory (MB), disk size (GB) and disk count, for example on a small test machine. Hyperlite sets no ceiling of its own otherwise, like Proxmox or vSphere. | none (technical ceilings only) |
| `HYPERLITE_AUTH_SIGNING` | `local`: the API signs sessions itself, for the tests and a development backend without hyperlite-daemon. Never on a node. | unset (hyperlite-daemon signs) |
| `HYPERLITE_VAR_DIR`, `HYPERLITE_ETC_DIR` | Where the data and the node's secrets live (`app/core/paths.py`); changed only for development and tests. | `/var/lib/hyperlite`, `/etc/hyperlite` |
| `HYPERLITE_ENV_LABEL` | Label of a non-production installation (for example `DEV` or `STAGING`, 24 characters at most), shown in the dashboard's top bar, on the sign-in page and in the tab title. Leave unset in production. | none |
| `HYPERLITE_API_DOCS` | `1` serves the interactive API documentation (`/docs`, `/redoc`, `/openapi.json`). They answer without authentication, so leave it off outside development: the accounts an administrator chose open it from Administration › API instead. | off |
| `HYPERLITE_HSTS_MAX_AGE` | When set (seconds), HTTPS responses carry `Strict-Transport-Security`. Only with a certificate browsers trust: with a self-signed one, browsers would then refuse to let users past the certificate warning. | off |
| `HYPERLITE_AUDIT_RETENTION_DAYS` | Audit log entries older than this many days are deleted (checked once a day); `0` keeps everything. Only until an administrator sets the retention in **Activity › Audit log › Retention**, which wins. Successful reads (the lists the dashboard polls) are not audited; failed ones are. | `365` |
| `HYPERLITE_TRUSTED_PROXIES` | Comma-separated addresses of reverse proxies in front of Hyperlite. Their `X-Forwarded-For` header is then used for the client address of the sign-in locks and the audit log; from any other peer the header is ignored. | none |
| `HYPERLITE_TUNNEL_PORTS` | Guest ports reachable through workstation tunnels (see [workstation-access.md](workstation-access.md)); empty disables tunnels. | `22,3389` |
| `HYPERLITE_TUNNEL_IDLE_TIMEOUT_S` | A tunnel without traffic is closed after this many seconds. | `3600` |
| `HYPERLITE_TUNNEL_MAX_PER_USER` | Open tunnels per user. | `20` |
| `HYPERLITE_CLI_TOKEN_DAYS` | Lifetime of the token of a workstation signed in with `hyperlite login`. | `30` |
| `HYPERLITE_CFS_SHADOW` | `1` forces shadow mode on: every change of the mirrored tables is copied into the local `hyperlite-cfs` daemon and the differences are reported (`GET /cfs/shadow`); SQLite stays the source of truth. Without it, shadow mode is turned on and off from Administration › Replicated configuration. See `cfs/README.md`, shadow mode. | off |
| `HYPERLITE_CFS_SOCKET` | Socket of the `hyperlite-cfs` daemon used by shadow mode. | `/run/hyperlite-cfs/socket` |
| `HYPERLITE_NODE_NAME` | This node's name in the configuration every node of a cluster shares (lowercase letters, digits, `.` and `-`, not `local`). Without it, the name recorded at the first start, taken from the host name. See `cfs/README.md`. | the host name |

Installer and build-time variables:

| Variable | Used by | Purpose |
|---|---|---|
| `HYPERLITE_APT_URL` | `installer/build-iso.sh`, `installer/postinstall.sh` | APT repository baked into the appliance ISO (default: the public mirror). |
| `HYPERLITE_SKIP_RESTART` | package `postinst` | Set by the update endpoint so the package does not restart the service it is running inside. |

Profile and policy names are French wire values (see [api.md](api.md)).

## Files

From 1.1 the files live where Proxmox VE keeps its own (`app/core/paths.py`); up to 1.0 they were all under
`/root/hyperlite`, and the first start of 1.1 moves them (`scripts/migrate_layout.sh`).

| Path | Content | Proxmox VE |
|---|---|---|
| `/usr/lib/hyperlite` | The code and its Python environment (the package's). | `/usr/share/perl5/PVE` |
| `/usr/share/hyperlite/www` | The web interface, built. | `/usr/share/pve-manager` |
| `/etc/hyperlite/priv/env` | Secrets and environment (root only). | `/etc/pve/priv` |
| `/etc/hyperlite/priv/ssh/` | Automation key (VMs, containers) and cluster keys. | `/etc/pve/priv`, `/root/.ssh` |
| `/etc/hyperlite/priv/authkey.key`, `/etc/hyperlite/authkeys.json` | The key pair hyperlite-daemon signs sessions with (renewed every day) and the public keys the API verifies them with. | `/etc/pve/priv/authkey.key`, `/etc/pve/authkey.pub` |
| `/etc/hyperlite/local/` | The web interface's certificate and key; replace them to use your own (or Node › Certificate). | `/etc/pve/local/pve-ssl.*` |
| `/var/lib/hyperlite/hyperlite.db` | SQLite database (WAL, root only). | `/var/lib/pve-cluster/config.db` |
| `/var/lib/hyperlite/template/iso`, `template/vm` | ISO images, VM templates. | `/var/lib/vz/template/iso` |
| `/var/lib/hyperlite/dump`, `dump/export`, `dump/container` | VM backups (the default directory), VM exports, container backups. | `/var/lib/vz/dump` |
| `/var/lib/hyperlite/import` | Disks uploaded to be imported. | `/var/lib/vz/import` |
| `/var/lib/hyperlite/tmp`, `k3s`, `bin`, `update-backups`, `previous` | Uploads in progress, Kubernetes files, tools, update archives, the package of the previous version. | |

## Ports

The service listens on TCP 8000 (HTTPS). Live migration uses libvirt's default migration ports between hosts. NFS pools require the NFS ports between the host and the server.
