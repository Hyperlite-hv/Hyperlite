# Hyperlite

Hyperlite is a self-hosted virtualization manager: a FastAPI backend that drives libvirt/QEMU-KVM (virtual machines) and LXC (containers), and a React web dashboard. It aims at the day-to-day experience of tools such as Proxmox VE or VMware vSphere on hardware you own.

> **Project status: early stage.** Hyperlite is developed by a small team and has been tested on a few physical machines and many throw-away VMs. There is no stable release yet, interfaces may change, and it has **not** been independently security-audited. Read [Limitations](#limitations) before running it anywhere that matters.

## Features

- **Virtual machines**: create, start/stop, delete, clone, snapshots, resource limits (cgroups), unattended installation of Debian/Ubuntu/RHEL-family ISOs, disk import/export.
- **Containers**: LXC containers built from a Debian base or from any Docker Hub / OCI image (no Docker daemon needed), with a web SSH terminal.
- **Storage**: local directory pools, shared NFS pools, ZFS pools (VMs on zvols, native ZFS snapshots).
- **Networking**: virtual networks (NAT, isolated, bridge, VLAN tags), per-VM firewall (libvirt nwfilter) and per-network firewall (iptables on the bridge).
- **Cluster**: several hosts managed through `qemu+ssh://` (no agent to install), live migration, basic high availability (failure detection and alert; recovery is always started by a human).
- **Backups**: hot and cold VM backups, schedules, retention.
- **Operations**: metrics history (Prometheus format available), audit journal, task tracking, job engine, outgoing notifications (webhook, email).
- **Security**: local accounts, optional TOTP two-factor authentication, API tokens, optional OpenID Connect single sign-on, fine-grained permissions (roles, groups, pools, per-resource ACL).
- **Web console**: VNC console and SSH terminals in the browser, host shell for administrators.

See [docs/features.md](docs/features.md) for details.

For Windows guests, including Windows Server 2025, see [the Windows installation guide](docs/windows.md)
for attaching VirtIO driver media and making the system disk visible in Windows Setup.

## Requirements

- A 64-bit x86 Linux host with hardware virtualization (KVM). Debian 12 and 13 are the tested platforms.
- libvirt and QEMU (`qemu-kvm`, `libvirt-daemon-system`), plus `libvirt-daemon-driver-lxc` for containers.
- Python 3.11 or newer.
- Node.js 20.19 or newer, only to build the dashboard from source.

Hyperlite runs as `root` (it manages libvirt, networks, storage and a host shell). Install it on a dedicated machine or VM.

## Installation

### Option 1: appliance ISO

Download [`hyperlite-appliance-amd64.iso`](https://github.com/Hyperlite-hv/Hyperlite/releases/download/appliance-iso-latest/hyperlite-appliance-amd64.iso) (with its `.sha256` and `.sig` files, see [docs/deployment.md](docs/deployment.md) to verify them), write it to a USB stick and boot the target machine. The installation is unattended and **erases the disks**. See [docs/deployment.md](docs/deployment.md) for the details.

### Option 2: APT repository on an existing Debian

Replace `<repository-url>` with the address of the Hyperlite APT repository (the public mirror is `https://hyperlite-hv.github.io`; see [docs/deployment.md](docs/deployment.md) for its limitations):

```bash
curl -fsSL <repository-url>/hyperlite-archive-keyring.asc | gpg --dearmor -o /usr/share/keyrings/hyperlite-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] <repository-url> stable main" > /etc/apt/sources.list.d/hyperlite.list
apt update && apt install hyperlite
```

The service listens on `https://<host>:8000` with a self-signed certificate. The initial administrator password is written to `/root/.hyperlite-initial-password`.

### Option 3: from source (development)

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

| Document | Content |
|---|---|
| [docs/webui-test-matrix.md](docs/webui-test-matrix.md) | Web UI end-to-end coverage matrix and how to run it |
| [docs/onboarding.md](docs/onboarding.md) | Access and working rules for a new contributor and their AI assistant |
| [docs/architecture.md](docs/architecture.md) | Components, data model, security model |
| [docs/features.md](docs/features.md) | Feature reference and known scope limits |
| [docs/configuration.md](docs/configuration.md) | Environment variables and files |
| [docs/deployment.md](docs/deployment.md) | Installation, updates, APT repository, ISO, publishing |
| [docs/api.md](docs/api.md) | API conventions and the French wire format |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, checks, workflow |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, security notes |

## Limitations

- Early-stage software; no stability promise for the API, the database schema or the packaging.
- The service and the host shell run as root. Do not expose port 8000 to untrusted networks.
- The appliance installer asks for the `root` password; the initial Hyperlite `admin` password is random and shown on the console banner. Change it after the first sign-in.
- The TLS certificate is self-signed; browsers will warn.
- High availability has no fencing (no STONITH): a failed node is detected and reported, but VM recovery on another node is a manual, human decision.
- ZFS pools are created on loopback files by the web interface (single node); Ceph is not supported.
- The automated test suite covers the backend logic and API (unit and API tests); the dashboard has lint and build checks but no automated UI tests in CI yet. Hypervisor operations (libvirt, ZFS, NFS, migration) are mostly verified by hand on real machines.
- The API and the database use French identifiers and values (see [docs/api.md](docs/api.md)).

## License

Hyperlite is distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, study, modify and share it for noncommercial purposes (personal use, hobby projects, research, education, nonprofit organizations, and testing). Commercial use, including offering it as a paid or hosted service, requires a separate permission from the author. If you want to evaluate Hyperlite for a business purpose, please ask first by opening an issue or contacting the maintainer through the repository. This is not legal advice; read the license text.

Third-party components keep their own licenses, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
