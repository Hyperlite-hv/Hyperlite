# Features

This is a reference of what Hyperlite does today and where its scope stops. For how to use each feature in the web interface, see the [user guide](user-guide.md).

## Virtual machines

- Lifecycle: create, start, stop (graceful or forced), restart, delete.
- Hyperlite Tools (the standard QEMU guest agent, `qemu-guest-agent`): when it runs in a VM, shutdowns and reboots go through it, the VM's IP address comes from the guest (also with a static IP or on a bridge), and hot backups freeze the guest file systems for a consistent copy. Cloud-image VMs install it at first boot; without it everything works as before (ACPI, DHCP lease, crash-consistent snapshot).
- Creation from a preinstalled cloud image (Debian 12, configured through cloud-init) or from an ISO, installed by hand through the console as on Proxmox VE, whatever the system.
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

- A cluster of equal nodes, as on Proxmox VE: created on one node and joined from the others in the dashboard (the **Cluster** page, in the sidebar's Cluster group). Every node runs Corosync and `hyperlite-cfs`, which hold the configuration (users, permissions, nodes, HA, schedules, settings...) on every node ([design](design/hyperlite-cfs.md)). Any node serves the dashboard and the API, with the same accounts and sessions; there is no controller to lose. Each node runs the jobs of the VMs it hosts, and what concerns the whole cluster (HA watching, the alert about a node gone down) runs on one node at a time, taken over by another within seconds when it goes.
- From any node: every node's tasks and audit log in one list, and the backups of a VM of another node (back up, schedule, restore, verify, delete; its files are browsed from its own node's dashboard).
- A primary node (**Make primary** on the Cluster page) counts twice in the quorum: with one other node it keeps working alone, so a second machine that is often off, or reached over a slower link, never blocks it. Otherwise a two-node cluster needs a QDevice, as on Proxmox.
- Quorum, as on Proxmox: a node cut off from the majority changes nothing until it is back (the dashboard and the API refuse every change there, VM start and stop included; scheduled backups, replication and automatic deletion wait; VMs set to start at boot start once the node has the quorum). A change still on its way when a node is cut off is dropped if the others changed the same thing meanwhile: the majority's version is kept and the conflict is in the audit log.
- Register remote hosts by SSH instead (a host across a WAN, where Corosync cannot run): view and manage their VMs from one dashboard.
- Live migration between hosts, with a compatibility diagnostic (CPU, QEMU/libvirt versions, machine types, storage, networks) before migrating.
- Node maintenance mode: draining live-migrates the running VMs to a chosen node, one migration task each, and lists the VMs that stay with the reason (stopped, iSCSI disks, compatibility blockers, name taken). A node in maintenance receives no new VM or container and is never a migration or HA recovery target.
- Configuration copy, for hosts driven over SSH: the controller copies its configuration (database without telemetry, and its keys) to every node every 15 minutes and right after a change; if the controller is lost, `hyperlite-promote` on a node takes over, refusing while the old controller still answers. See [Taking over when the controller is lost](cluster-failover.md).
- HA protection per VM, for disks on a share that locks across nodes (NFS, SMB: QEMU's own image locks then refuse a disk another node runs); a share whose locks stay on one node (`nolock`, `local_lock=`, `nobrl`) is refused. An administrator can still recover a VM by hand; a node with a BMC is powered off through it first, and the recovery waits until it reads off.
- Automatic HA ([design](design/ha-automatic.md)): the leader, with the quorum, restarts the HA VMs of a member whose status is older than 60 s once it is known to be stopped (its BMC powered it off and read it off, or its armed watchdog had its reset delay plus 30 s); only the VMs that were running, one by one by priority, on their first preferred node or the online member with the most free memory, never one in maintenance; at most 3 automatic restarts per VM per hour. Without proof the member stopped, its VMs are left to an administrator. The returning node undefines the VMs that moved. Admission control (`warn` by default, `strict`, `off`) checks that the loss of any member leaves room for its HA VMs. Hosts driven over SSH keep a dry run that only says what it would do.

## Backups

Hot (transient external snapshot) and cold backups of VMs, schedules (daily, weekly, monthly) per VM or per group of VMs (all, a tag or a pool), retention by count or by days, weeks and months, restore in place or to a new VM, and file-level restore (browse a backup's disks and download files without restoring the VM).

Every backup has a manifest (`manifest.json`: each file with its role, size and SHA-256). **Verify** recomputes every checksum and runs `qemu-img check` on the images; every backup is also verified automatically within a week (one at a time, never during a backup). A corrupted backup is shown as such, with what is wrong, audited and notified (`verify_backup` event). UEFI VMs keep their firmware state: the NVRAM (boot entries, Secure Boot keys) and the TPM state (BitLocker keys) are saved with the disks and put back on restore, in place or as a new VM. See the [design](design/backups-pro.md) for the next steps (block disks, deduplication, encryption).

Two sites: each site backs its VMs up to a storage of the other one, and **Backups › Recovery of another site** restores a lost site's VMs as new VMs on the surviving site, from their latest backup, after an integrity check. See [site recovery](site-recovery.md). **Replication to another site** copies VMs to the other site every few minutes (incremental, QEMU dirty bitmaps, a full copy each day), so the surviving site loses at most one interval of changes.

## Updates

- One update per node for Hyperlite and the node's Debian packages together. Before it starts: the new version and its release notes, the Debian packages that change (security ones counted) and whether a reboot will be needed. The node is backed up first; a watchdog puts the previous version back when the new one does not answer after the restart. Running VMs keep running, and a reboot is never done for you.
- **Go back to** the version before the last update (its package is kept on the node, so it also works offline).
- A channel per node: `stable`, the releases, or `test`, every change merged for the next release. Going back to stable never installs an older version.
- Debian's security updates every night (03:30 by default, per node, or off): security packages only, never Hyperlite, never a reboot; a pending reboot is notified.
- A node without network: every version publishes a signed update file (`hyperlite-update-amd64.tar`: Hyperlite, the Debian packages it needs and the release notes). The node checks the signature and every file before showing what will change.
- Rolling update of a cluster (**Administration › Replicated configuration › Cluster update**): a plan read from every node first, then one node after the other, each put in maintenance and emptied by live migration, updated, rebooted when needed (VMs that cannot move are stopped for it only if chosen in the plan), and taken out of maintenance before the next one. A failure stops the run on that node, which stays in maintenance.
- Versions follow semver from 1.0.0 (`1.x.y`, the package carrying the epoch `1:` so APT upgrades from the dated versions).

## Observability and automation

Continuous host and VM metrics with history (Prometheus text format available), an audit journal with filters, persisted tasks with progress, a small job engine to run commands on hosts or VMs, and outgoing notifications (webhook, SMTP email) for significant events.

## Accounts and access control

Local users, optional two-factor authentication (a TOTP code, or security keys and passkeys through WebAuthn: YubiKey, Windows Hello, Touch ID, a phone), personal API tokens, optional OIDC single sign-on (roles mapped from IdP groups), custom roles, groups and pools, per-VM and per-container ACL, brute-force protection.

Security keys are added from **Account security** and asked for after the password (either a key or the code works when both are set up). A key is bound to the host name the dashboard was opened with when it was added: browsers only offer WebAuthn over HTTPS through a host name (or on `localhost`), never on an IP address. Removing a key takes the password.

## Portability

At start-up and on demand Hyperlite detects the capabilities of the host (CPU, RAM, storage, network, QEMU/libvirt versions, Secure Boot, optional components) and reports them in a *Compatibility* view. A preflight check runs before installation.

## Known scope limits

- A cluster needs a low-latency network between its nodes (Corosync). Hosts driven over SSH depend on one controller, whose loss needs a manual takeover.
- Automatic HA needs a BMC (IPMI, Redfish) or an armed watchdog on each member; Intel AMT is not available yet (Debian packages no `fence_amt_ws`). VM monitoring (a hung guest reset in place) is not done yet.
- VLAN tags on VLAN-aware Linux bridges, Open vSwitch or SR-IOV networks; host interfaces (bridges, bonds, VLANs) are not yet created from the dashboard (#512).
- Backups of ZFS and iSCSI disks are refused; there is no deduplication or encryption of backups yet.
- VMs on ZFS pools are not live-migratable.
- ZFS pools created from the interface use loopback files and are local to one node.
- Ceph is not supported.
- Unattended installation from an ISO is not offered (since 1.0), for any system: the cloud image and templates are the prepared paths.
