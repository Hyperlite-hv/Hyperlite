# Design: professional-grade backups

Status: **proposal, waiting for the maintainer's decision.** No code is written before it is accepted.

## 1. Where we are (`app/core/backups.py`)

- **Every backup is a full copy.** Cold (VM stopped): `qemu-img convert` of each disk. Hot: a transient
  external snapshot, a copy of the frozen base, then `blockCommit` and pivot. Since Hyperlite Tools, the hot
  snapshot is quiesced (file systems frozen) when the guest agent runs.
- **ZFS zvols and iSCSI LUNs are refused** (`block_disk_error`): the code only copies disk *files*.
- **Integrity**: a SHA-256 is computed only when the backup has a single disk (`checksum_sha256` column), and it
  is never re-checked afterwards.
- **Retention**: keep the last N backups of a VM (`retention_count`), applied after each run.
- **Target**: a local directory (`cible_dir`, default under the data directory). No deduplication, no
  encryption, no off-site copy.
- A backup of 100 GB of disk with 1 GB changed since yesterday copies 100 GB again every time.

## 2. Goals, in order

1. **Incremental** backups: after one full backup, copy only the blocks changed since the previous one.
2. **ZFS and iSCSI** VMs can be backed up (and restored).
3. **Integrity**: every backup can be verified, automatically and on demand.
4. **Clear retention**: keep the last N, plus daily, weekly and monthly ones. Never break an incremental
   chain.
5. **Deduplication and encryption**, and an off-site target.

## 3. Options per goal

### 3.1 Incremental

| Option | How | For | Against |
|---|---|---|---|
| A. libvirt backup API with checkpoints (`checkpointCreateXML`, `backupBegin`, available in this libvirt-python) | libvirt keeps a persistent **dirty bitmap** per disk, stored in the qcow2 file. `backupBegin` in *push* mode writes a full or incremental image; each backup creates the next checkpoint | Native and live. No external snapshot or `blockCommit` dance. Works with the guest agent quiesce | Bitmaps need qcow2 v3 disks. Checkpoints must be managed with snapshots (libvirt refuses some combinations). Incrementals form a chain to track |
| B. Our own diff (hash of blocks between two copies) | Read the whole disk, compare with the last backup | No libvirt feature needed | Reads the whole disk every time: not really incremental in I/O |
| C. External tool (restic/borg) on the full image | Each run sends a full image; the tool deduplicates | Dedup and encryption for free | Still reads and sends the whole disk every run |

**Recommended: A.** It is what Proxmox (`qemu-server` bitmaps) and oVirt use, and it reads only the changed
blocks. The incremental file is a qcow2 whose backing file is the previous backup, so a restore is a single
`qemu-img convert` of the newest file, which follows the chain.

### 3.2 ZFS and iSCSI disks

- **ZFS zvol**: `zfs snapshot pool/vol@hyperlite-<ts>`, quiesced through the guest agent (fsfreeze around the
  snapshot), then:
  - *full*: `zfs send pool/vol@snap`, stored as a stream, or converted to qcow2 through the snapshot device
    (`snapdev=visible`) with `qemu-img convert`;
  - *incremental*: `zfs send -i @previous @snap`. ZFS tracks the changed blocks itself.
  Restore: `zfs receive`, or `qemu-img convert` to a new zvol.
- **iSCSI LUN**: the array's snapshots are not reachable from Hyperlite, so the backup reads the block device.
  With the VM running, use option A's `backupBegin`: libvirt can back up a raw block disk in push mode (full
  only, since there are no persistent bitmaps on a raw LUN). With the VM stopped, `qemu-img convert` of the
  device. Incremental for iSCSI is out of scope for now (it would need a qcow2 overlay on top of the LUN, which
  changes how the VM uses it).

### 3.3 Integrity

- A **manifest** per backup (`manifest.json` next to the files): disks, formats, sizes, SHA-256 of **every**
  file, chain parent, VM configuration digest.
- A **verify** action and a weekly job: recompute the checksums, run `qemu-img check` (and `qemu-img compare`
  for the newest restored image, when time allows), and mark the backup `verifie` or `corrompu` with the date.
- Later: an optional **test restore**: boot a restored copy on an isolated network, wait for the guest agent
  ping, then delete it. This is the only proof that a backup really restores.

### 3.4 Retention

- A **GFS** policy per job: keep the last *N*, plus *D* daily, *W* weekly and *M* monthly backups (the same
  idea as Proxmox's keep-last/keep-daily/...). The `retention_count` of today becomes keep-last.
- **Chain-aware**: an incremental whose parent must be deleted is first merged into it (`qemu-img commit` or
  rebase), or the policy keeps the parent. A retention run never leaves an incremental without its base.
- A **dry run**: "this policy would delete these 7 backups", shown before saving it.

### 3.5 Deduplication, encryption, off-site

| Option | For | Against |
|---|---|---|
| restic (or borg) repository as a backup **target** | Dedup, encryption, compression, many backends (local, SFTP, S3), mature and verifiable (`restic check`) | An external tool to ship. A restore goes through the tool. Incremental images are already small, so the dedup gain is mostly across VMs |
| Proxmox Backup Server as a target | Designed exactly for this | Its protocol is tied to Proxmox's format; heavy to integrate |
| Our own chunk store | Full control | Months of work, high risk for data safety |

**Recommended: restic as an optional target**, added after the other steps. The native directory target stays
the default. Encryption keys are handled like the other secrets (Fernet at rest) and **shown once** to the
admin with a warning: a lost repository key means lost backups.

## 4. Risks

| Risk | Mitigation |
|---|---|
| A broken chain makes several backups useless | Chain-aware retention, a verify job, a new full backup every *K* incrementals (for example weekly) |
| Checkpoints and snapshots interfering (libvirt refuses some operations when both exist) | Tests of every combination; a clear refusal message instead of a failed backup; documented limits |
| Old disks in qcow2 v2 (no bitmaps) | Detect it, fall back to full backups, and show "incremental not available: upgrade the disk format" with the command |
| A long backup while the VM writes heavily | Push mode with bandwidth limits; hot backups stay optional per job |
| A lost encryption key | Show once, require an explicit acknowledgement, encourage an off-site copy of the key |
| Restore of an old chain on a newer QEMU | Standard qcow2, no proprietary format; the test restore catches problems early |

## 5. Steps (one pull request each)

1. **Manifest and verify**: a SHA-256 for every file, a manifest, a verify action and a weekly job. No format
   change, immediate value.
2. **ZFS and iSCSI full backups**: zfs snapshot/send, and backupBegin or qemu-img for LUNs. The refusal in
   `block_disk_error` goes away for them.
3. **Incremental qcow2** with checkpoints (option A): full plus incrementals, restore through the chain, a
   periodic full.
4. **ZFS incremental** with `zfs send -i`.
5. **GFS retention**, chain-aware, with a dry run.
6. **restic target** (dedup, encryption, off-site), optional.
7. **Test restore** (optional, isolated network, guest agent ping).

Each step keeps the old backups restorable; nothing is migrated in place.

## 6. Questions for the maintainer

1. Where should backups go in the target setup: a local disk, the NAS over NFS, or off-site (S3 or SFTP)?
2. Which size of VMs and how many: this sets the full/incremental cadence (for example a weekly full plus
   daily incrementals)?
3. Is restic acceptable as an additional package, or should off-site copies stay out of Hyperlite for now?
4. Step 1 (manifest and verify) can start right away without any risk. Shall I?
