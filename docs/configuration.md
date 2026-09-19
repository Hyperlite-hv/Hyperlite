# Configuration

Hyperlite is configured through environment variables, normally set in `/root/hyperlite/.env` (read by systemd through `EnvironmentFile`). The package creates this file on first installation with fresh secrets and never overwrites it on upgrade.

| Variable | Purpose | Default |
|---|---|---|
| `HYPERLITE_SECRET_KEY` | Secret used to sign session tokens. Must be long and random. | random per process (sessions are lost on restart) |
| `HYPERLITE_INITIAL_ADMIN_PASSWORD` | Password of the `admin` account created on first start. Ignored if an admin already exists. | none |
| `HYPERLITE_ENCRYPTION_KEY` | Fernet key used to encrypt secrets stored in the database. Generated automatically into `.env` when missing. | generated |
| `HYPERLITE_PROFILE` | Force the deployment profile (`homelab`, `standard`, `avance`). | detected |
| `HYPERLITE_ALLOCATION` | Force the resource allocation policy (`limites`, `surallocation`, `libre`). | profile default |
| `HYPERLITE_VM_MAX_VCPU`, `HYPERLITE_VM_MAX_MEMORY_MB`, `HYPERLITE_VM_MAX_DISK_GB`, `HYPERLITE_VM_MAX_DISKS` | Override the per-VM limits derived from the host. | derived from the host |
| `HYPERLITE_APP_DIR` | Application directory assumed by the preflight check. | `/root/hyperlite` |

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
