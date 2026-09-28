# Features

This is a reference of what Hyperlite does today and where its scope stops.

## Virtual machines

- Lifecycle: create, start, stop (graceful or forced), restart, delete.
- Hyperlite Tools (the standard QEMU guest agent, `qemu-guest-agent`): when it runs in a VM, shutdowns and reboots go through it, the VM's IP address comes from the guest (also with a static IP or on a bridge), and hot backups freeze the guest file systems for a consistent copy. Cloud-image VMs install it at first boot; without it everything works as before (ACPI, DHCP lease, crash-consistent snapshot).
- Creation from a preinstalled cloud image (Debian 12, configured through cloud-init) or from an ISO. Unattended installation is supported for Debian-family (preseed), Ubuntu (autoinstall) and RHEL-family (kickstart) media; other ISOs boot for a manual installation through the VNC console.
- Import an existing disk (qcow2, raw, vmdk, vdi, vhd) and export a VM disk.
- Snapshots (libvirt internal snapshots; native ZFS snapshots for VMs on ZFS pools, disk only), cloning, conversion to template and deployment from a template.
- Resource limits and priorities through cgroups; live disk and network interface hot-plug.
- Firmware: legacy BIOS, UEFI, or UEFI with Secure Boot and a TPM 2.0 (Windows 11); see [Windows guests](windows.md).
- CPU affinity (**Hardware → Options and limits**): run a VM only on chosen host CPUs, or one host CPU per vCPU for stable latency. Applied at once, also to a running VM. On a host with several NUMA cells, a VM whose CPUs are all in one cell also takes its memory from that cell (at the next start). Pinning names this host's CPUs: unpin a VM before migrating it to a host that lacks them.
- Host devices (**Hardware → Host devices**, administrators): give a VM a USB device (also while it runs) or a PCI card such as a GPU or a network card (VM stopped, IOMMU required; the whole IOMMU group goes together). Hyperlite never offers what the host itself needs: the card showing the host console, a network card with an address or in a bridge, a disk controller with a mounted file system, swap or ZFS pool, PCI bridges. See [Host device passthrough](passthrough.md).
- Grow a disk (live or stopped) and move a disk to another directory or NFS pool (live, with a block copy and a pivot, or stopped).
- Optional automatic deletion of VMs that stay stopped for a configurable number of days (never applies to a running or HA-protected VM, with a warning about 24 hours before).
- No ceiling of Hyperlite's own on a VM's vCPU, memory or disks (as in Proxmox or vSphere): only technical bounds, optional administrator caps (`HYPERLITE_VM_MAX_*`), and a non-blocking warning when a value exceeds the hardware.

## Containers

LXC containers through libvirt's native driver. The root file system comes from a debootstrapped Debian 12 base or from any Docker Hub / OCI registry image (`skopeo` and `umoci`, no Docker daemon). Clone, backup and restore are supported; there is no instantaneous snapshot because libvirt's LXC driver does not provide one.

## Storage

Directory pools, NFS pools (shared storage, prerequisite for HA and live migration) and ZFS pools (VM disks as zvols). Pool creation and removal from the interface; removing a directory or NFS pool can detach it without deleting its files.

## Networking

Virtual networks in NAT, isolated or bridge mode, DHCP ranges, VLAN tags on VM interfaces, a per-VM firewall (libvirt nwfilter) and a per-network firewall (dedicated iptables chain on the bridge, re-applied at start-up).

## Cluster, migration and high availability

- Register remote hosts by SSH; view and manage their VMs from one dashboard.
- Live migration between hosts, with a compatibility diagnostic (CPU, QEMU/libvirt versions, machine types, storage, networks) before migrating.
- Node maintenance mode: draining live-migrates the running VMs to a chosen node, one migration task each, and lists the VMs that stay with the reason (stopped, iSCSI disks, compatibility blockers, name taken). A node in maintenance receives no new VM or container and is never a migration or HA recovery target.
- Basic HA: protected VMs (disks on shared storage) are monitored; when a node goes down an alert is raised and an administrator can recover the VM on another node. Recovery is never automatic, and an SSH-based best-effort fence is attempted first. There is no STONITH.

## Backups

Hot (transient external snapshot) and cold backups of VMs, schedules (daily, weekly, monthly), retention by count, restore in place or to a new VM.

Every backup has a manifest (`manifest.json`: each file with its role, size and SHA-256). **Verify** recomputes every checksum and runs `qemu-img check` on the images; every backup is also verified automatically within a week (one at a time, never during a backup). A corrupted backup is shown as such, with what is wrong, audited and notified (`verify_backup` event). UEFI VMs keep their firmware state: the NVRAM (boot entries, Secure Boot keys) and the TPM state (BitLocker keys) are saved with the disks and put back on restore, in place or as a new VM. See the [design](design/backups-pro.md) for the next steps (block disks, incremental backups, GFS retention).

## Observability and automation

Continuous host and VM metrics with history (Prometheus text format available), an audit journal with filters, persisted tasks with progress, a small job engine to run commands on hosts or VMs, and outgoing notifications (webhook, SMTP email) for significant events.

## Accounts and access control

Local users, optional TOTP two-factor authentication, personal API tokens, optional OIDC single sign-on (roles mapped from IdP groups), custom roles, groups and pools, per-VM and per-container ACL, brute-force protection.

## Portability

At start-up and on demand Hyperlite detects the capabilities of the host (CPU, RAM, storage, network, QEMU/libvirt versions, Secure Boot, optional components) and reports them in a *Compatibility* view. A preflight check runs before installation.

## Known scope limits

- No fencing / STONITH; HA recovery is manual.
- VMs on ZFS pools are not live-migratable.
- ZFS pools created from the interface use loopback files and are local to one node.
- Ceph is not supported.
- Windows unattended installation is not supported.
