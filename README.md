# Hyperlite

Hyperlite is a self-hosted virtualization manager: a FastAPI backend that drives libvirt/QEMU-KVM (virtual machines) and LXC (containers), and a React web dashboard. It aims at the day-to-day experience of tools such as Proxmox VE or VMware vSphere on hardware you own.

> **Project status: early stage.** Hyperlite is developed by a small team and has been tested on a few physical machines and many throw-away VMs. There is no stable release yet, interfaces may change, and it has **not** been independently security-audited. Read [Limitations](#limitations) before running it anywhere that matters.

## Features

- **Virtual machines**: create, start/stop, delete, clone, snapshots, templates, cloud-init, resource limits (cgroups), unattended installation of Debian/Ubuntu/RHEL-family ISOs, disk import/export, UEFI with Secure Boot and TPM 2.0, USB and PCI passthrough, CPU pinning.
- **Containers**: LXC containers built from a Debian base or from any Docker Hub / OCI image (no Docker daemon needed), with a web SSH terminal; Kubernetes (k3s) clusters on Hyperlite VMs.
- **Storage**: local directory pools, shared NFS pools (with the NFS version chosen at creation), ZFS pools (VMs on zvols, native ZFS snapshots), iSCSI targets.
- **Networking**: virtual networks (NAT, isolated, bridge on a host bridge, NIC, bond or VLAN interface), per-VM firewall (libvirt nwfilter) and per-network firewall (iptables on the bridge). A VLAN tag on a VM interface is accepted only on a network that can carry it (Open vSwitch, SR-IOV).
- **Cluster**: several hosts managed from one controller through `qemu+ssh://` (no agent to install), live migration with a compatibility check, node maintenance mode, a copy of the configuration on every node with a manual takeover (`hyperlite-promote`), and high availability that detects a failed node and shows what automatic recovery would do (dry run; recovery is started by a human).
- **Backups**: hot and cold VM backups with a checksummed manifest and automatic verification, UEFI NVRAM and TPM state included, schedules per VM or per group, retention by count or daily/weekly/monthly, file-level restore, replication to another site every few minutes (incremental), and recovery of a lost site.
- **Operations**: metrics history (Prometheus format, export to InfluxDB and Graphite), audit journal, task tracking, job engine, outgoing notifications (webhook, email).
- **Security**: local accounts, LDAP / Active Directory, optional OpenID Connect single sign-on, two-factor authentication (TOTP or WebAuthn security keys and passkeys), API tokens, fine-grained permissions (roles, groups, pools, per-resource ACL).
- **Web console**: VNC console and SSH terminals in the browser, host shell for administrators.
- **Workstation access**: the `hyperlite` client opens SSH or remote desktop from the user's own computer through a tunnel over the server's HTTPS port, with sign-in through the web interface (SSO and 2FA apply), per-VM permission and audit.

See [docs/features.md](docs/features.md) for details and the [user guide](docs/user-guide.md) for how to use each feature.

For Windows guests, including Windows Server 2025, see [the Windows installation guide](docs/windows.md).
The Windows profile defaults to SATA storage to avoid loading a separate storage driver.
See [guest compatibility and hardware profiles](docs/guest-compatibility.md) for tested scope and firmware limitations.

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
| [docs/user-guide.md](docs/user-guide.md) | User guide: how to use every feature of the web interface |
| [docs/webui-test-matrix.md](docs/webui-test-matrix.md) | Web UI end-to-end coverage matrix and how to run it |
| [docs/onboarding.md](docs/onboarding.md) | Access and working rules for a new contributor and their AI assistant |
| [docs/architecture.md](docs/architecture.md) | Components, data model, security model |
| [docs/features.md](docs/features.md) | Feature reference and known scope limits |
| [docs/configuration.md](docs/configuration.md) | Environment variables and files |
| [docs/workstation-access.md](docs/workstation-access.md) | SSH and remote desktop from a workstation (`hyperlite` client) |
| [docs/deployment.md](docs/deployment.md) | Installation, updates, APT repository, ISO, publishing |
| [docs/api.md](docs/api.md) | API conventions and the French wire format |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, checks, workflow |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, security notes |

## Limitations

- Early-stage software; no stability promise for the API, the database schema or the packaging.
- The service and the host shell run as root. Do not expose port 8000 to untrusted networks.
- The appliance installer asks for the `root` password; the initial Hyperlite `admin` password is random and shown on the console banner. Change it after the first sign-in.
- The TLS certificate is self-signed by default (browsers will warn); a certificate can be imported or obtained through ACME (Let's Encrypt).
- One controller drives the cluster. Its configuration is copied to every node every 15 minutes, and taking over after its loss is a manual command. A Proxmox-like replicated configuration (Corosync and `hyperlite-cfs`) is being built, see [docs/design/hyperlite-cfs.md](docs/design/hyperlite-cfs.md).
- Automatic high availability runs as a dry run only: a failed node is detected and reported with what would be done, but VM recovery on another node is a manual, human decision (no fencing yet).
- ZFS pools are created on loopback files by the web interface (single node); Ceph is not supported.
- CI runs the backend unit and API tests, the dashboard lint and build, a Playwright end-to-end suite against a real backend and libvirt (QEMU without KVM), and the `hyperlite-cfs` tests with sanitizers and fuzzing. Multi-node operations (NFS, ZFS, migration, HA) are still mostly verified by hand on real machines.
- The API and the database use French identifiers and values (see [docs/api.md](docs/api.md)).

## License

Hyperlite is distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, study, modify and share it for noncommercial purposes (personal use, hobby projects, research, education, nonprofit organizations, and testing). Commercial use, including offering it as a paid or hosted service, requires a separate permission from the author. If you want to evaluate Hyperlite for a business purpose, please ask first by opening an issue or contacting the maintainer through the repository. This is not legal advice; read the license text.

Third-party components keep their own licenses, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
