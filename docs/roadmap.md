# Roadmap

What Hyperlite plans to build, in priority order. The big items get a design document in `docs/design/` first, and
no code is written until the maintainer accepts that design (the same rule as `docs/design/ha-automatic.md`). What is
already shipped is in [features.md](features.md).

The goal: a hypervisor that a DevOps team drives entirely through its API and tools. It should be simpler than Proxmox
to run in a cluster, and it should not promise what a small team cannot maintain.

## P1: next

### Automation and Infrastructure as Code
- **Official Terraform / OpenTofu provider.** Resources: VMs, disks, networks, pools, backup jobs, users and roles.
  It is built on the personal API tokens and on the Go client (`cli/`). Proxmox only has community providers.
- **Declarative API.** A file describes the wanted state (VMs, networks, jobs). `plan` shows the differences and
  `apply` makes the cluster match. It can be run from a Git repository (GitOps).
- **Ansible collection.** Modules for the same resources, plus a dynamic inventory of the VMs.

### High availability and load
- **Automatic HA with reliable fencing**: see [ha-automatic.md](design/ha-automatic.md). The watcher already runs as
  a dry run, and its takeover by another node when the leading node goes down was checked on three nested nodes.
- **Rule-based load balancing (DRS).** When a node's CPU or memory stays above a threshold, Hyperlite proposes live
  migrations, then can apply them on its own. The administrator chooses which (manual, proposed or automatic). Rules
  can keep two VMs together or apart (affinity and anti-affinity), and nodes in maintenance are skipped. No
  prediction at first: thresholds that last a while, and measured migration costs.

### Backups without freezing the VM
- **Backups without an internal snapshot.** QEMU copies a block to the backup just before the VM overwrites it (its
  backup job with copy-before-write, or fleecing). The VM keeps writing at full speed during the backup.
- **Application hooks around the freeze.** The guest file systems are already frozen through the guest agent. Add
  scripts that run before and after the freeze (for a database to flush), measure each freeze, and report it.
- **Incremental backups of every disk type**: see [backups-pro.md](design/backups-pro.md). That includes ZFS zvols
  and iSCSI LUNs, which are refused today.

## P2: then

### Storage
- **VM disks on an existing Ceph cluster (RBD)**: see [ceph-rbd.md](design/ceph-rbd.md). Hyperlite connects to Ceph,
  it does not install or run it.
- **Guided storage setup and health.** An assistant to connect NFS, iSCSI or Ceph. Checks of latency, free space
  and paths, with plain-language alerts before something breaks.

### Kubernetes and VMs together
- **Kubernetes drives Hyperlite.** A CSI driver for Hyperlite storage, then a Cluster API provider, so the k3s
  clusters on Hyperlite VMs grow and shrink by themselves.
- **VMs from Kubernetes.** Hyperlite VMs described as Kubernetes objects (in the spirit of KubeVirt), for teams that
  want one tool for both.

### Hardening
- **QEMU sandbox.** Each QEMU process already runs as `libvirt-qemu` under an AppArmor profile; add seccomp
  filtering (libvirt's `seccomp_sandbox`) and check the profiles on every release.
- **Management interface on its own network.** Choose the address the dashboard and API listen on, and warn when it
  is reachable from a VM network.
- **Hardware failure warnings.** Read SMART, the memory error counters (EDAC) and machine-check events. Warn before a
  disk or the memory fails, and offer to drain the node.
- **VLAN-aware Linux bridges**: see [network.md](design/network.md).

## P3: later

- **Confidential VMs (AMD SEV-SNP, Intel TDX).** The VM's memory is encrypted and protected from the host itself, for
  defence and critical operators. It depends on the hardware.
- **Load prediction.** Once the DRS has a history, learn the daily and weekly cycles to migrate before a peak instead
  of after it.

## Out of scope, and why

- **Our own distributed storage, a "simpler Ceph".** It would take years, and losing data is the worst failure a
  hypervisor can have. Hyperlite connects to proven storage instead (NFS, iSCSI, ZFS, Ceph) and makes it simple to use.
- **A formally proved micro-kernel hypervisor (seL4 type).** It would be a different product from KVM. The proof
  would not cover device emulation either, where most VM escapes are found. Hyperlite reduces that risk with
  confinement (AppArmor, seccomp), updates and confidential VMs.
- **Fault Tolerance, a second VM running in lock-step with no interruption.** KVM has no maintained support for it,
  and even VMware limits it to small VMs. Automatic HA restarts a VM in seconds; that is the target.
- **Snapshots or backups "at the nanosecond".** No system captures a running application consistently without a short
  pause. The target is a VM that is never slowed down by a backup and a freeze measured in milliseconds.
