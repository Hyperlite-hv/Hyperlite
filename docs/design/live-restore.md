# Design: live restore (#513)

Status: **done**: part 1 (backups kept as files, 1.12.0) and part 2 (backups in a backup store).

The reference platform starts a VM restored from a dedicated backup server at once and copies its disks while it runs. Hyperlite now
does the same for a backup kept as files (a directory backup), restored as a new VM.

## How

1. Each restored disk is a new qcow2 file whose **backing file is the backup's own image** (`qemu-img create -b`).
   QEMU opens a backing file read-only: the backup is never written. The VM's writes go to the new file; its reads of
   what it has not written come from the backup.
2. The VM is defined as an ordinary restore defines it, then **started at once** by hyperlite-daemon.
3. libvirt's **block pull** (QEMU's `block-stream`) copies the rest of each disk into its new file while the VM runs,
   and drops the backing reference at the end. The copy's progress is the restore task's.
4. At the end every new disk is checked to read **no backing file**. Then the VM no longer needs the backup.
5. **A copy that fails** (the backup unreadable, a disk full) stops the VM and removes it with its new disks: a disk
   is never left half restored. The task says why.
6. libvirt gives a running VM's backing files to QEMU's account and does not give them back: the backup's images get
   their owner and mode back once the copy ends, whatever happened.
7. The backup is marked as being read for the whole restore, the copy included: it cannot be deleted under the VM.

`app/core/live_restore.py`, `backups.restore_files(..., live=True)`, `POST /backups/{id}/restore` with
`"live": true`. The dashboard's backups of a VM offer **Live restore** next to **New VM**.

## Limits

- **A new VM only**, not over the original one.
- **A backup kept as files** must have qcow2 images (Hyperlite's directory backups do). A backup in a store is served
  instead (part 2, below).
- **The VM's disks go where a restore puts them** (the images directory), in qcow2.
- While the copy runs the VM reads the backup's storage: a slow backup disk makes a slow VM until it ends.

## Verified

On a production node with libvirt 11.3, on a VM and images made for the test: the VM running from the start, the
copy ending, the new disk identical to the backup (`qemu-img compare`), no backing file left, and the backup's owner
and mode given back (libvirt had left it to `libvirt-qemu`).

## Part 2: a backup in a store

A store's disk is an index of 4 MiB chunks, compressed and maybe encrypted: QEMU cannot open it. hyperlite-daemon
serves it's live restore:

- **`app/core/nbd_export.py`**: a read-only NBD server on a Unix socket, in the daemon, for the time of the restore.
  Each read is answered from the chunks it falls in, read from the store and decrypted (`Store.get`); the 16 chunks read
  last are kept, since the block pull and the guest read on. The least of the protocol: fixed newstyle negotiation,
  `NBD_OPT_GO`, `NBD_OPT_INFO` and `NBD_OPT_EXPORT_NAME`, simple replies (structured replies and metadata contexts
  refused, which QEMU and libnbd fall back from), `NBD_CMD_READ` and `NBD_CMD_DISC`; a write is refused (`EPERM`), a
  damaged or missing chunk is an I/O error, never wrong data. No nbdkit: its Python plugin runs the distribution's
  Python, not the service's (`docs/design/backup-store.md`, section 5).
- **Where**: `/run/hyperlite/backup/live-<vm>-<disk>.sock`, QEMU's user and group only, the directory QEMU's AppArmor
  profile already lets it reach (`scripts/ensure-backup-apparmor.sh`).
- **The disk**: a qcow2 file whose backing is `nbd+unix:///?socket=...` (raw); then as part 1: the VM starts, the block
  pull copies, no backing file is left, and the server closes. A copy that fails removes the VM and its disks.
- **Verified** on a production node, with an encrypted store made for the test: the VM running on the export from the
  start, the copy ending, the new disk identical to the backup (`qemu-img compare`), the socket gone. `nbdinfo`,
  `nbdcopy` and `qemu-img compare` against the server directly, plain and encrypted stores.
