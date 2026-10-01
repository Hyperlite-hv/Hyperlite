# Features

This is a reference of what Hyperlite does today and where its scope stops. For how to use each feature in the web interface, see the [user guide](user-guide.md).

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

Kubernetes clusters: one k3s server VM and worker VMs, built from Hyperlite's Debian cloud image (k3s binaries checked against the release checksums, no Internet access needed to bootstrap), handed over as an encrypted kubeconfig.

## Storage

Directory pools, NFS pools (shared storage, prerequisite for HA and live migration; the NFS version is chosen at creation, never negotiated), ZFS pools (VM disks as zvols) and iSCSI targets (whole LUNs created on the storage side). Pool creation and removal from the interface; removing a directory or NFS pool can detach it without deleting its files.

## Networking

Virtual networks in NAT, isolated or bridge mode (on a host bridge, or on a NIC, bond or VLAN interface through macvtap), DHCP ranges and reservations, VLAN tags on VM interfaces where the network can carry them (Open vSwitch or SR-IOV; refused elsewhere, since libvirt would not start the VM), a per-VM firewall (libvirt nwfilter) and a per-network firewall (dedicated iptables chain on the bridge, re-applied at start-up).

## Cluster, migration and high availability

- Register remote hosts by SSH; view and manage their VMs from one dashboard.
- Live migration between hosts, with a compatibility diagnostic (CPU, QEMU/libvirt versions, machine types, storage, networks) before migrating.
- Node maintenance mode: draining live-migrates the running VMs to a chosen node, one migration task each, and lists the VMs that stay with the reason (stopped, iSCSI disks, compatibility blockers, name taken). A node in maintenance receives no new VM or container and is never a migration or HA recovery target.
- Configuration copy: the controller copies its configuration (database without telemetry, and its keys) to every node every 15 minutes and right after a change; if the controller is lost, `hyperlite-promote` on a node takes over, refusing while the old controller still answers. See [Taking over when the controller is lost](cluster-failover.md).
- Basic HA: protected VMs (disks on shared storage) are monitored; when a node goes down an alert is raised and an administrator can recover the VM on another node. Recovery is never automatic, and an SSH-based best-effort fence is attempted first.
- Automatic HA, **dry run** ([design](design/ha-automatic.md)): every 10 s the nodes carrying protected VMs are probed (libvirt and SSH). A node is suspect, then failed after configurable thresholds; a node whose libvirt fails while SSH answers is not counted as failed. For a failed node each protected VM shows what automatic HA *would* do: nothing if this controller may be the isolated one (it must reach a majority of the voters, a witness included), manual recovery on two nodes without a witness or without fencing, otherwise "power off through IPMI/Redfish/AMT or wait for the leases, then restart on <node>". Nothing is powered off or restarted. Per-node fencing settings (IPMI, Redfish, Intel AMT, leases only) are stored encrypted and **Test** only reads the power state (the `fence-agents` package provides the agents). **Leases** checks that libvirt's `lockd` lock manager is on.

## Backups

Hot (transient external snapshot) and cold backups of VMs, schedules (daily, weekly, monthly) per VM or per group of VMs (all, a tag or a pool), retention by count or by days, weeks and months, restore in place or to a new VM, and file-level restore (browse a backup's disks and download files without restoring the VM).

Every backup has a manifest (`manifest.json`: each file with its role, size and SHA-256). **Verify** recomputes every checksum and runs `qemu-img check` on the images; every backup is also verified automatically within a week (one at a time, never during a backup). A corrupted backup is shown as such, with what is wrong, audited and notified (`verify_backup` event). UEFI VMs keep their firmware state: the NVRAM (boot entries, Secure Boot keys) and the TPM state (BitLocker keys) are saved with the disks and put back on restore, in place or as a new VM. See the [design](design/backups-pro.md) for the next steps (block disks, deduplication, encryption).

Two sites: each site backs its VMs up to a storage of the other one, and **Backups › Recovery of another site** restores a lost site's VMs as new VMs on the surviving site, from their latest backup, after an integrity check. See [site recovery](site-recovery.md). **Replication to another site** copies VMs to the other site every few minutes (incremental, QEMU dirty bitmaps, a full copy each day), so the surviving site loses at most one interval of changes.

## Observability and automation

Continuous host and VM metrics with history (Prometheus text format available), an audit journal with filters, persisted tasks with progress, a small job engine to run commands on hosts or VMs, and outgoing notifications (webhook, SMTP email) for significant events.

## Accounts and access control

Local users, optional two-factor authentication (a TOTP code, or security keys and passkeys through WebAuthn: YubiKey, Windows Hello, Touch ID, a phone), personal API tokens, optional OIDC single sign-on (roles mapped from IdP groups), custom roles, groups and pools, per-VM and per-container ACL, brute-force protection.

Security keys are added from **Account security** and asked for after the password (either a key or the code works when both are set up). A key is bound to the host name the dashboard was opened with when it was added: browsers only offer WebAuthn over HTTPS through a host name (or on `localhost`), never on an IP address. Removing a key takes the password.

## Portability

At start-up and on demand Hyperlite detects the capabilities of the host (CPU, RAM, storage, network, QEMU/libvirt versions, Secure Boot, optional components) and reports them in a *Compatibility* view. A preflight check runs before installation.

## Known scope limits

- One controller: the configuration is copied to the nodes and a takeover is manual. The replicated configuration (Corosync and `hyperlite-cfs`, as Proxmox VE does with `pmxcfs`) is in progress.
- No fencing yet; automatic HA is a dry run and recovery is manual.
- VLAN tags only on Open vSwitch or SR-IOV networks; VLAN-aware Linux bridges are the next step (`docs/design/network.md`).
- Backups of ZFS and iSCSI disks are refused; there is no deduplication or encryption of backups yet.
- VMs on ZFS pools are not live-migratable.
- ZFS pools created from the interface use loopback files and are local to one node.
- Ceph is not supported.
- Windows unattended installation is not supported.
