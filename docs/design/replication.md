# Decision: storage replication between nodes

Status: **decided: not built for now.** This page records why, what covers the need today, and what would reopen
the question.

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
