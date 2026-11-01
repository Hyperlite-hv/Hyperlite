# ZFS pools on the node's disks (#419)

Status: **done (2026-11-01)**, but verified on simulated disks only: the test node has a single disk, its system's, and
its ZFS module is refused by Secure Boot until its DKMS key is enrolled (at the machine's console).

Proxmox VE makes ZFS pools from Disks › ZFS on whole disks, as a single disk, a mirror or RAIDZ1/2/3, shows their health
and grows a RAIDZ by one disk (9.0). Hyperlite made pools on a loopback file only, to try ZFS. Now:

## What it does

- **The free disks** (`app/core/host_disks.py`, `GET /storage/zfs/disks`): whole disks with no partition, no file system
  or volume signature (LVM, ZFS, RAID, swap...), nothing mounted, not read-only, not removable, at least 1 GiB, and no
  VM's disk (a disk passed through whole included). Named by their `/dev/disk/by-id` path (the bus and serial, then the
  WWN), which stays the same across reboots. Anything else is neither offered nor taken: hyperlite-daemon checks the
  disks again against its own list before any `zpool` command.
- **A pool on them** (`POST /storage` with `zfs_layout` and `zfs_disks`): `single` (one disk, or a stripe), `mirror`
  (2+), `raidz1` (3+), `raidz2` (4+), `raidz3` (5+), Proxmox's minimums. `ashift=12` (4 KiB sectors, right for every
  disk sold today, as Proxmox), `autotrim=on`, `compression=lz4`, `mountpoint=none` (zvols only). The dashboard asks to
  confirm that the disks are erased. The pool on a file stays, to try ZFS without a disk.
- **Its health** (`GET /storage/{pool}/zfs`, *ZFS health* in the pool's menu): `zpool status -j --json-int -P`
  (OpenZFS 2.3), each vdev and disk with its state and read, write and checksum errors, the log, cache, spare, special
  and dedup devices, and the last scrub or rebuild (its progress while it runs).
- **Scrub** (`POST /storage/{pool}/zfs/scrub`): started or stopped by hand. Debian's `zfsutils-linux` already scrubs
  every pool monthly from cron, as Proxmox relies on.
- **Disks** (`POST /storage/{pool}/zfs/disks`):
  - `attach`: one disk to a vdev or a disk: a single disk becomes a mirror, a mirror gets a copy more, a RAIDZ grows by
    one disk (RAIDZ expansion, its data spread again in the background);
  - `add`: a new vdev beside the others, for room (`zpool` refuses one less redundant than the pool's own);
  - `replace`: a failed or failing disk by a free one; ZFS rebuilds it (resilver).
- **Deleting a pool on disks** clears their ZFS labels and their partition table: they are free again.
- **Watching** (hyperlite-scheduler, every 10 minutes): a pool that leaves ONLINE (a disk failed or missing) is notified
  once (event `zfs_pool`), and once when it is healthy again. A degraded mirror or RAIDZ works unseen until the next
  failure loses it.
- **A kernel updated without its headers**: DKMS cannot build the ZFS module for it, and ZFS stops working at the next
  boot (seen on the test node). `status()` now says `module_not_built`, with the fix (`apt install
  linux-headers-amd64`, whose headers then follow every kernel update).

## Not done

- Moving a VM on ZFS to another node (live or not) and replicating zvols between nodes (`zfs send`), as Proxmox's
  storage replication: the next part of #419. A VM on ZFS is backed up into a backup store, which reads block disks.
- A spare disk, log, cache or special device added from the dashboard (shown when present).

## Tests

`tests/test_zfs_disks.py`: the free disks (the system's partitioned disk, LVM, ZFS, mounted, a VM's, removable, too
small refused; by-id names), each layout's command, every refusal before `zpool` runs, the health parsed from OpenZFS
2.3's JSON (as `zpool-status(8)` shows it), attach / add / replace and their refusals, a destroyed pool's disks given
back, the notification once each way, the daemon checking the disks itself, the routes. `tests/test_storage_support.py`:
the module not built for the running kernel. The dashboard: `poolForm.test.js` (enough disks for the layout).
