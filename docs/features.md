# Features

This is a reference of what Hyperlite does today and where its scope stops.

## Virtual machines

- Lifecycle: create, start, stop (graceful or forced), restart, delete.
- Creation from a preinstalled cloud image (Debian 12, configured through cloud-init) or from an ISO. Unattended installation is supported for Debian-family (preseed), Ubuntu (autoinstall) and RHEL-family (kickstart) media; other ISOs boot for a manual installation through the VNC console.
- Import an existing disk (qcow2, raw, vmdk, vdi, vhd) and export a VM disk.
- Snapshots (libvirt internal snapshots; native ZFS snapshots for VMs on ZFS pools, disk only), cloning, conversion to template and deployment from a template.
- Resource limits and priorities through cgroups; live disk and network interface hot-plug.
- Optional automatic deletion of VMs that stay stopped for a configurable number of days (never applies to a running or HA-protected VM, with a warning about 24 hours before).
- Resource limits offered by the interface are derived from the host (cores, RAM, free disk) and an allocation policy (limits, overcommit, free), not from fixed constants.

## Containers

LXC containers through libvirt's native driver. The root file system comes from a debootstrapped Debian 12 base or from any Docker Hub / OCI registry image (`skopeo` and `umoci`, no Docker daemon). Clone, backup and restore are supported; there is no instantaneous snapshot because libvirt's LXC driver does not provide one.

## Storage

Directory pools, NFS pools (shared storage, prerequisite for HA and live migration) and ZFS pools (VM disks as zvols). Pool creation and removal from the interface; removing a directory or NFS pool can detach it without deleting its files.

## Networking

Virtual networks in NAT, isolated or bridge mode, DHCP ranges, VLAN tags on VM interfaces, a per-VM firewall (libvirt nwfilter) and a per-network firewall (dedicated iptables chain on the bridge, re-applied at start-up).

## Cluster, migration and high availability

- Register remote hosts by SSH; view and manage their VMs from one dashboard.
- Live migration between hosts, with a compatibility diagnostic (CPU, QEMU/libvirt versions, machine types, storage, networks) before migrating.
- Basic HA: protected VMs (disks on shared storage) are monitored; when a node goes down an alert is raised and an administrator can recover the VM on another node. Recovery is never automatic, and an SSH-based best-effort fence is attempted first. There is no STONITH.

## Backups

Hot (transient external snapshot) and cold backups of VMs, schedules (daily, weekly, monthly), retention by count, restore in place or to a new VM.

## Observability and automation

Continuous host and VM metrics with history (Prometheus text format available), an audit journal with filters, persisted tasks with progress, a small job engine to run commands on hosts or VMs, and outgoing notifications (webhook, SMTP email) for significant events.

## Accounts and access control

Local users, optional TOTP two-factor authentication, personal API tokens, optional OIDC single sign-on (roles mapped from IdP groups), custom roles, groups and pools, per-VM and per-container ACL, brute-force protection.

## Portability

At start-up and on demand Hyperlite detects the capabilities of the host (CPU, RAM, storage, network, QEMU/libvirt versions, Secure Boot, optional components) and reports them in a *Compatibility* view. A preflight check runs before installation. Deployment profiles (homelab, standard, advanced) only change default values.

## Known scope limits

- No fencing / STONITH; HA recovery is manual.
- VMs on ZFS pools are not live-migratable.
- ZFS pools created from the interface use loopback files and are local to one node.
- Ceph is not supported.
- Windows unattended installation is not supported.
