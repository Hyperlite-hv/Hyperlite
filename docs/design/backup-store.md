# A backup store as Proxmox Backup Server's (#337, #420)

Status: **proposal** (2026-10-24). Section 2 holds the maintainer's choices; no code before the design is accepted.
It follows `docs/design/backups-pro.md`, whose steps 1 (manifest and verify) and 5 (daily, weekly and monthly
retention) are done, and replaces its steps 2 to 4 and 6.

## 1. Where we are

- **Every backup is a full copy** (`app/core/backups.py`): `qemu-img convert` of each disk, cold, or a copy behind a
  transient external snapshot, hot. 100 GB of disk with 1 GB changed since yesterday is 100 GB read and written again.
- **VMs with a block disk are refused** (`block_disk_error`): ZFS zvols, LVM volumes, iSCSI LUNs.
- **Nothing is shared**: ten VMs made from one template hold ten copies of the same system.
- **No encryption**: a backup on a NAS or off site is readable by whoever reads the share.
- What works and stays: a manifest with every file's SHA-256, verification (on demand and scheduled), the NVRAM and
  TPM state of UEFI VMs, retention by count or daily/weekly/monthly, file restore (libguestfs), replication to another
  site (`app/core/replication.py`, libvirt checkpoints).

## 2. Decisions (maintainer, 2026-10-24)

| Question | Proxmox VE | vSphere | Hyperlite |
|---|---|---|---|
| Incremental, deduplication | Proxmox Backup Server: 4 MiB chunks named by their digest, deduplicated across every guest; QEMU's dirty bitmaps, only changed blocks read; every backup a complete image | Changed Block Tracking (CBT) through VADP; storage and deduplication left to a third party (Veeam...) | **A chunk store as PBS's**, inside Hyperlite: no other server to run |
| Encryption | Optional, on the client, AES-256-GCM, one key per client, a master key to recover | Left to the third party | **Optional per store, on the client, AES-256-GCM, with a master recovery key** (chosen as the most complete and safe: section 6) |
| Today's backups | (none) | (none) | **Readable for ever, never rewritten**, importable into a store on demand (chosen as the safest) |

## 3. The store

A **store** is a directory: a local disk, or a share mounted on every node (NFS, SMB), as a PBS datastore. Its layout:

```
<store>/
  .store.json                     id, format version, chunk size, whether encrypted (and the key's fingerprint)
  .chunks/0000/ ... .chunks/ffff/ the chunks, by the first four hex digits of their digest (65,536 directories)
  vm/<vm uuid>/<time>/            one backup: index.fidx per disk, manifest.json, nvram, tpm/ (small, kept whole)
  .lock                           what garbage collection takes (section 8)
```

- **A chunk** is 4 MiB of a disk (the last one shorter), named by its digest: SHA-256 of its content, or, in an
  encrypted store, an HMAC-SHA-256 keyed with a key derived from the store's key, so a digest says nothing of the
  content to whoever reads the share (PBS does the same). Stored as a blob: a header (magic, format, CRC-32), then
  the content compressed with zstd (level 1), encrypted when the store is (AES-256-GCM, a random 96-bit nonce per
  chunk, the tag in the header). A chunk of zeros is never stored: the index marks it.
- **An index** (`index.fidx`) is a disk's size and the list of its chunks' digests, in order: a complete image, never
  a delta. **There is no chain**: deleting any backup never makes another unusable, and restoring the oldest is as
  fast as restoring the newest.
- **Writes are atomic**: a chunk is written to a temporary name then renamed (a rename is atomic on a local file system
  and on an NFS server); a chunk already there is never written again. The index and the manifest are written last,
  the same way: a backup that stopped midway is not a backup and never listed; its chunks go at the next garbage
  collection.
- **The manifest** of today, extended: each index's SHA-256, the store's id, the previous backup it read from (for
  information, never needed to restore).
- **One store for the whole cluster**: every node writes into the same store on a share at once (atomic renames, no
  shared lock for a backup); deduplication then works across the nodes too.

## 4. Backing up a VM

Through libvirt's backup API in **pull mode** (`backupBegin`): QEMU exports each disk, frozen at the start, as a
read-only NBD device on a Unix socket, while the VM runs; Hyperlite reads it, cuts it in chunks, writes the chunks the
store does not hold yet. The same path for **every disk type**, files and block devices: the refusal of block disks
goes away (#337).

- **What is read**:
  - a **qcow2 disk** keeps a persistent dirty bitmap, a libvirt checkpoint per backup to this store (as replication
    already does): only the 4 MiB regions with a changed block are read, the others taken from the previous backup's
    index. A VM that writes 1 GB a day reads about 1 GB a day, whatever its size;
  - a **raw or block disk** (zvol, LVM, LUN) has no persistent bitmap, and libvirt makes checkpoints of qcow2 disks
    only (PBS keeps an in-memory bitmap through QEMU patches Debian's QEMU does not carry): it is read whole at each
    backup, and only its changed chunks are written (deduplication). Later, each storage's own change tracking can
    narrow the read: the changed ranges between two ZFS snapshots of a zvol (`zfs send -i`), between two LVM-thin
    snapshots (`thin_delta`);
  - a **stopped VM** is read the same way through `qemu-nbd` (read-only, `--snapshot` for a qcow2) on the disk itself.
- **Quiesced**: the guest agent freezes the file systems while the export starts, as today's hot backups.
- **The checkpoint and its store**: a checkpoint names the store and the index it belongs to; a bitmap is used only
  when the previous backup of that VM in that store is the one it was taken with, else the disk is read whole once.
  Replication's checkpoints and the backups' are distinct and coexist (libvirt keeps several bitmaps).
- **Reading and writing in parallel**: the NBD reads, the digests, compression and encryption (`hashlib`,
  `cryptography`, zstd, all releasing Python's lock) in a few threads, with a bandwidth limit per job as today.
- **The helper**: libnbd's Python binding is the distribution's (`python3-libnbd`), so the reading runs in a helper
  under the system Python, as file restore runs its libguestfs helper (`file_restore_helper.py`).

## 5. Restoring

A backup served as a disk: **nbdkit** with a small Python plugin reads the index and the chunks (decrypting when the
store is encrypted) and exports the image, read-only, on a Unix socket. Then:

- **to a new VM or over the old disks**: `qemu-img convert` from that export to the disk of the pool chosen (qcow2,
  raw, zvol, LVM), zero chunks skipped;
- **file restore**: libguestfs opens the export (`nbd://` on the socket) instead of a qcow2 file, the rest unchanged;
- **live restore** (#513): the VM starts at once on a copy-on-read overlay whose backing is the export, its disk filled
  in the background, as PBS's live restore. #513's design settles it; this design gives it the export.

## 6. Encryption

- **Off by default, chosen per store at its creation**, with a warning when the store is a share: a store's
  encryption never changes afterwards (its chunks would not deduplicate with the new ones).
- **The key** (32 random bytes) is made by Hyperlite and kept in the cluster's secrets (`priv/`, hyperlite-cfs), so
  every node backs up and restores; it is shown **once**, as a file to download and as a printable code with a QR
  code, with the warning that a lost key is a lost store.
- **The master recovery key**, as PBS's: an RSA-4096 key pair; Hyperlite holds only the public half and adds, to
  every backup's manifest, the store key encrypted with it. The private half is given once to the administrator
  (file and paper) and never kept: with it, a backup can be read even if every node and the cluster's secrets are
  lost. Optional, offered at the store's creation.
- **What stays readable**: the index's sizes and the manifest's VM name and dates, as PBS's; the content, the chunks'
  digests and the NVRAM and TPM state are encrypted.

## 7. Verification, retention, garbage collection, off-site copy

- **Verification**: every chunk of a backup read, its CRC and digest checked (decrypted first). A damaged chunk is
  renamed `.bad` and the backups that use it marked damaged; the next backup of any VM that still holds that content
  writes it again, which repairs them, as PBS's.
- **Retention**: the policies of today (last N, daily, weekly, monthly) on the store's backups; with no chain, a
  policy deletes exactly what it says.
- **Garbage collection**, as PBS's: mark (touch the access time of every chunk an index names, explicitly with
  `utimensat`, whatever the mount's `noatime`), then sweep (delete the chunks not touched since the mark began, minus
  24 h 5 min, so a backup running meanwhile is never cut). One at a time per store in the whole cluster: a lock of
  hyperlite-cfs, named after the store's id. Scheduled weekly, and on demand, with what it freed.
- **Off-site copy** (sync): copy to another store the backups it lacks, chunk by chunk, only the missing chunks; the
  other store is a directory (a share at the other site) or another Hyperlite cluster over its API (HTTPS, pinned).
  Encrypted chunks are copied as they are: the other site never needs the key.

## 8. What else changes

- **Jobs**: a backup job targets a store or a directory (today's format), the store being the default for new jobs
  once step 3 ships; existing jobs are not changed.
- **Today's backups** stay listed, verified and restored as now. **Import** (on demand, per backup or per VM): read
  each disk through `qemu-img`, cut in chunks into a store, the old one kept until the administrator deletes it.
- **Containers** keep today's format: their file archives need variable-size chunks (PBS's dynamic index), a later
  step.
- **The API**: new routes for the stores (`/backup-stores`), new optional fields on jobs and backups (`store`); no
  field removed, as API v1 requires.
- **Packaging**: `nbdkit`, `nbdkit-plugin-python`, `python3-libnbd` (Debian 13), and the `zstandard` wheel; nothing
  else.

## 9. Steps

Each one ships with its tests (unit, the e2e suite, the package lab) and keeps today's backups working:

1. **The store** (format, writer, reader, verification, garbage collection, retention) and **full backups into it** of
   every disk type, cold and hot (pull mode), with restore through the export: the refusal of block disks goes away.
2. **Incremental reading**: checkpoints and dirty bitmaps of qcow2 disks.
3. **Encryption** and the master recovery key; the store becomes the default for new jobs.
4. **Off-site copy** to another store or another cluster; **import** of today's backups.
5. **File restore** from a store, and the export for **live restore** (with #513).

## 10. Tests

- **The format**: a store written then read back bit for bit (random disks, sizes not multiple of 4 MiB, zero
  regions, a disk of 0 bytes); a chunk damaged on disk found by verification and repaired by the next backup; an
  index or a chunk cut in the middle never listed; encryption: a wrong key refused, a chunk's tampering caught by its
  tag, the master key recovering a backup with nothing else.
- **Concurrency**: two nodes backing up into one store at once on NFS, the same template's chunks written once;
  garbage collection during a backup never deletes a chunk the backup uses (the lab, three nodes on one share).
- **Incremental**: after a backup, 10 MiB written in the guest, the next one reads about 12 MiB (three regions), and
  restores identical to the guest's disk (`qemu-img compare`); the checkpoint of another store, or a missing one,
  falls back to a whole read and stays correct.
- **Every disk type** in the lab: qcow2, raw, zvol, LVM-thin, iSCSI, hot and cold, restored to another type.
- **Throughput**: measured on the lab and stated in the pull request (target: limited by the disks, not the CPU, on
  one core per job).

## 11. Risks

| Risk | Mitigation |
|---|---|
| A bug in our format loses data | A format simple enough to describe in one page, versioned; every backup verified once written; today's directory backups kept as an option; the restore of every disk type in the lab before each release |
| A lost key | Shown once with an explicit acknowledgement, the master recovery key, the off-site copy carrying the encrypted chunks |
| A dirty bitmap that does not match the disk | Used only from the exact checkpoint of the previous backup in the same store; any doubt reads the disk whole |
| A store on a share that disappears during a backup | The backup fails, nothing is listed; the next one writes the missing chunks |
| Garbage collection on a share with unreliable access times | The access time set explicitly, and checked on the store at its creation (written, read back); refused if the share does not keep it |
