# Configuration

Hyperlite is configured through environment variables, normally set in `/root/hyperlite/.env` (read by systemd through `EnvironmentFile`). The package creates this file on first installation with fresh secrets and never overwrites it on upgrade.

| Variable | Purpose | Default |
|---|---|---|
| `HYPERLITE_SECRET_KEY` | Secret used to sign session tokens. Must be long and random: the service refuses to start when it is set to fewer than 32 characters (`openssl rand -hex 32`). | random per process (sessions are lost on restart) |
| `HYPERLITE_INITIAL_ADMIN_PASSWORD` | Password of the `admin` account created on first start. Ignored if an admin already exists. | none |
| `HYPERLITE_ENCRYPTION_KEY` | Fernet key used to encrypt secrets stored in the database. Generated automatically into `.env` when missing. | generated |
| `HYPERLITE_VM_MAX_VCPU`, `HYPERLITE_VM_MAX_MEMORY_MB`, `HYPERLITE_VM_MAX_DISK_GB`, `HYPERLITE_VM_MAX_DISKS` | Optional caps on a VM's vCPU, memory (MB), disk size (GB) and disk count, for example on a small test machine. Hyperlite sets no ceiling of its own otherwise, like Proxmox or vSphere. | none (technical ceilings only) |
| `HYPERLITE_APP_DIR` | Application directory assumed by the preflight check. | `/root/hyperlite` |
| `HYPERLITE_ENV_LABEL` | Label of a non-production installation (for example `DEV` or `STAGING`, 24 characters at most), shown in the dashboard's top bar, on the sign-in page and in the tab title. Leave unset in production. | none |
| `HYPERLITE_API_DOCS` | `1` serves the interactive API documentation (`/docs`, `/redoc`, `/openapi.json`). They answer without authentication, so leave it off outside development: the accounts an administrator chose open it from Administration › API instead. | off |
| `HYPERLITE_HSTS_MAX_AGE` | When set (seconds), HTTPS responses carry `Strict-Transport-Security`. Only with a certificate browsers trust: with a self-signed one, browsers would then refuse to let users past the certificate warning. | off |
| `HYPERLITE_AUDIT_RETENTION_DAYS` | Audit log entries older than this many days are deleted (checked once a day); `0` keeps everything. Successful reads (the lists the dashboard polls) are not audited; failed ones are. | `365` |
| `HYPERLITE_TRUSTED_PROXIES` | Comma-separated addresses of reverse proxies in front of Hyperlite. Their `X-Forwarded-For` header is then used for the client address of the sign-in locks and the audit log; from any other peer the header is ignored. | none |
| `HYPERLITE_TUNNEL_PORTS` | Guest ports reachable through workstation tunnels (see [workstation-access.md](workstation-access.md)); empty disables tunnels. | `22,3389` |
| `HYPERLITE_TUNNEL_IDLE_TIMEOUT_S` | A tunnel without traffic is closed after this many seconds. | `3600` |
| `HYPERLITE_TUNNEL_MAX_PER_USER` | Open tunnels per user. | `20` |
| `HYPERLITE_CLI_TOKEN_DAYS` | Lifetime of the token of a workstation signed in with `hyperlite login`. | `30` |
| `HYPERLITE_CFS_SHADOW` | `1` copies every change of the mirrored tables into the local `hyperlite-cfs` daemon and reports the differences (`GET /cfs/shadow`); SQLite stays the source of truth. See `cfs/README.md`, shadow mode. | off |
| `HYPERLITE_CFS_SOCKET` | Socket of the `hyperlite-cfs` daemon used by shadow mode. | `/run/hyperlite-cfs/socket` |

Installer and build-time variables:

| Variable | Used by | Purpose |
|---|---|---|
| `HYPERLITE_APT_URL` | `installer/build-iso.sh`, `installer/postinstall.sh` | APT repository baked into the appliance ISO (default: the public mirror). |
| `HYPERLITE_SKIP_RESTART` | package `postinst` | Set by the update endpoint so the package does not restart the service it is running inside. |

Profile and policy names are French wire values (see [api.md](api.md)).

## Files

| Path | Content |
|---|---|
| `/root/hyperlite/.env` | Secrets and environment (mode 600). |
| `/root/hyperlite/hyperlite.db` | SQLite database (WAL). |
| `/root/hyperlite/data/tls/` | Self-signed certificate and key; replace them to use your own certificate. |
| `/root/hyperlite/data/ssh/` | Automation key (VMs, containers) and cluster keys. |
| `/root/hyperlite/data/isos`, `templates`, `vm-exports`, `imported-disks`, `tmp` | Uploaded and generated files. |

## Ports

The service listens on TCP 8000 (HTTPS). Live migration uses libvirt's default migration ports between hosts. NFS pools require the NFS ports between the host and the server.
