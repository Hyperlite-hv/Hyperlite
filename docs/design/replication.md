# Decision: storage replication between nodes

Status: **reopened on 2026-10-01 for replication between two sites (section 5), accepted with the decisions of
section 5.6, built in `app/core/replication.py`.** Sections 1 to 4 record the earlier decision about replication between nodes of one site.

## 1. The need

Storage replication copies a VM's disks to another node on a schedule (every few minutes), so that when the node
running it is lost, the VM starts on the other node from the last copy. It is the answer of clusters without shared
storage: less data at risk than a nightly backup, no NFS server or Ceph cluster to run.

## 2. Why not now

- **Only ZFS can do it well, and Hyperlite's ZFS pools are local file-backed pools.** Incremental replication needs
  block-level snapshots sent as differences (`zfs send -i`, `zfs receive`). Files on directory pools would have to
  be copied whole at each run, which does not scale past a few VMs. The ZFS support (app/core/zfs_storage.py) creates
  a pool on a file of the host, meant for trying ZFS; a replicated setup needs real ZFS pools with the same name on
  every node, which Hyperlite does not create or check today.
- **It changes what HA and migration assume.** HA recovery (app/core/ha.py) refuses a VM that is not on shared
  storage, because starting a copy elsewhere while the original may still run corrupts the disk. Recovery from a
  replica is a different promise: it loses the changes since the last run and must be sure the old node is fenced.
  Getting that wrong loses data silently; it needs its own design, fencing included (docs/design/ha-automatic.md).
- **It cannot be verified here.** Two nodes with real ZFS pools, a network cut and a node loss are the only honest
  test. Claiming it works from unit tests alone is not acceptable for a feature whose only job is to be there on the
  day a node dies.

## 3. What covers the need today

| Need | Today |
| --- | --- |
| Restart a VM elsewhere after a node loss | Disks on an NFS pool, HA protection (automatic restart on another node, after a best-effort fencing over SSH). |
| Keep copies off the node | Scheduled backups to another pool, NFS included, with retention, integrity check and restore (Backups page). |
| Move VMs without downtime | Live migration, with or without shared storage (the disk is copied when it is local). |
| Empty a node for maintenance | Maintenance mode: its VMs are migrated one after another. |

## 4. What would reopen it

- Real ZFS pools managed by Hyperlite on several nodes (created on disks, same name everywhere), or Ceph RBD
  (docs/design/ceph-rbd.md), whose replication is built into the storage itself and would make this feature
  unnecessary.
- A test bed with at least two nodes on real ZFS pools, to verify a recovery from a replica after a node loss.

When it is built, the outline is: a replication job per VM (target node, interval, number of snapshots kept) run as
a task; `zfs snapshot` then `zfs send -i` over the cluster's SSH link into `zfs receive -F` on the target; the VM's
definition copied along; recovery started only by an administrator or by HA after fencing, never on a plain
connection loss.

## 5. Reopened: replication between two sites (proposal, 2026-10-01)

### 5.1 The need

The production runs on two distant sites, each its own Hyperlite (a Corosync cluster needs a LAN). When a whole site
is lost, the other one restarts its VMs from what it holds of them (`docs/site-recovery.md`). With daily backups, a
day of data is lost. The maintainer asked for less: copies every few minutes.

### 5.2 How Proxmox does it, and what follows for Hyperlite

Proxmox's storage replication (`zfs send`) works only between nodes of one cluster and only on ZFS. Between sites,
Proxmox relies on Proxmox Backup Server: incremental backups (QEMU dirty bitmaps) that only read the changed blocks,
synchronised to a second server on the other site. The surviving site restores from there.

The same approach fits Hyperlite and reuses what exists:

| Piece | Today | To build |
|---|---|---|
| Copy to the other site | backups to an NFS pool of the other site | nothing |
| Restore on the surviving site | site recovery (`app/core/site_recovery.py`) | restore through an incremental chain |
| Small copies every few minutes | full backups only (a whole disk read and written each time) | **incremental backups**, step 3 of `docs/design/backups-pro.md` |

### 5.3 Proposal

1. **Incremental backups with libvirt checkpoints** (backups-pro section 3.1, option A). libvirt keeps a persistent
   dirty bitmap per qcow2 disk, and `backupBegin` writes only the blocks changed since the previous checkpoint into a
   qcow2 whose backing file is the previous backup. It works on any storage that holds qcow2 files (local directory,
   NFS) and needs no ZFS.
   - The chain is: one full backup, then incrementals. Every *K* incrementals (for example every day), a new full one
     starts a new chain, so a damaged link never costs more than a day.
   - Retention never deletes a backup another one depends on.
   - qcow2 v2 disks, which have no bitmaps, fall back to full backups, with the reason shown.
2. **A "replication" schedule**: a grouped backup job with an interval in minutes (15 by default) instead of a time of
   day, targeting the other site's storage, and a cap on how many run at once so the inter-site link is not
   saturated.
3. **Site recovery restores the newest point of a chain**: `qemu-img convert` of the newest incremental reads through
   its backing chain into one independent disk.
4. **A dashboard indicator per VM**: the age of its last copy on the other site, warned about past twice the interval.
5. **ZFS `send -i` stays a later option** for VMs on real ZFS pools, which Hyperlite does not create yet (section 2).

### 5.4 What it cannot promise

- Data loss is bounded by the interval (15 minutes by default), not zero: synchronous replication between distant
  sites would slow every write of every VM down to the link's latency.
- The first full copy of each VM crosses the link in full; on a slow link, it is better seeded by a backup carried on
  a disk.
- It cannot be verified in CI: QEMU's bitmaps need a real VM writing to its disk. The test plan is a real VM on two
  hosts, with an incremental chain restored on the second one, and the content compared.

### 5.5 Questions for the maintainer

- **R1.** The accepted data loss: 15 minutes, 1 hour, or 4 hours?
- **R2.** The bandwidth between the sites, and the number and size of the VMs: this decides whether every VM can
  follow the interval.
- **R3.** Approach: incremental backups (works on the current storage, no ZFS), or ZFS replication only (needs real ZFS
  pools on disks first)?

### 5.6 Decisions (maintainer, 2026-10-01)

- **R1: 15 minutes** of data at most: the replication interval defaults to 15 minutes.
- **R2: unknown.** Prudent defaults: two copies at once at most, a full copy at most once a day per VM, and a per-VM
  delay indicator, so a link that cannot keep up shows which VMs fall behind instead of failing silently.
- **R3: incremental backups**, as proposed in 5.3. ZFS replication stays a later option.

### 5.7 Verified on libvirt 10 and QEMU 8.2 (2026-10-01)

Tried on a real QEMU VM, with the libvirt Python binding Hyperlite uses:

- **A full backup, then an incremental one into a qcow2 whose backing file is the full one, gives the exact disk.**
  `backupBegin` in push mode with a new checkpoint, then `<incremental>` from that checkpoint with
  `VIR_DOMAIN_BACKUP_BEGIN_REUSE_EXTERNAL` into a target created beforehand with `qemu-img create -b`.
  `qemu-img compare` of the chain against the live disk reports identical images. The incremental only holds the
  changed clusters.
- **Writes made while the VM is stopped are tracked too.** The bitmaps live in the qcow2 file, and any QEMU block
  layer user (qemu-io, qemu-img) marks them. The next incremental, once the VM runs, includes those writes.
- **Constraints, all checked:**
  - `backupBegin` needs a running VM. A stopped VM whose disks did not change since its last copy is skipped. If they
    changed, a full cold copy starts a new chain.
  - While a checkpoint exists, libvirt refuses **"block operations"** ("cannot perform block operations while
    checkpoint exists"). That covers the transient external snapshot of today's hot backups, offline snapshots, and,
    by the same rule, disk moves and live migrations that copy the disk. Snapshots of a running VM still work.
  - A checkpoint **cannot be deleted while the VM is stopped**. When the VM runs, `checkpoint.delete()` removes it
    and its bitmap.
  - Removing a VM's checkpoint metadata leaves its bitmaps in the qcow2 file, and a later checkpoint of the same name
    then fails ("Bitmap already exists"). Checkpoint names must therefore be unique (a timestamp), and dropping a
    chain must also remove the bitmaps (`qemu-img bitmap --remove` when the VM is stopped).

Consequences for the implementation:

1. A VM under replication is backed up with `backupBegin` only, never with the external snapshot of today's hot
   backups.
2. Before an operation libvirt refuses while checkpoints exist (disk move, migration copying the disk, offline
   snapshot, disk resize), Hyperlite **drops the replication chain**: `checkpoint.delete()` on a running VM, or the
   metadata plus the bitmaps with `qemu-img` on a stopped VM. The next run then starts a new full copy. The operation
   tells the user that it will do so.
3. Only one checkpoint per disk is kept, the last one: each incremental creates the next checkpoint and deletes the
   previous one, so the qcow2 file never accumulates bitmaps.
