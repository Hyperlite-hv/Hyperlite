# Design: live restore (#513)

Status: **part 1 done** (backups kept as files). Part 2: backups in a backup store.

Proxmox VE starts a VM restored from Proxmox Backup Server at once and copies its disks while it runs. Hyperlite now
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

- **A new VM only**, not over the original one (as Proxmox's).
- **A backup kept as files**, whose disks are qcow2 images (Hyperlite's directory backups are). A backup in a backup
  store is refused for now: its disks are indexes of chunks, which need serving as an NBD export first (part 2).
- **The VM's disks go where a restore puts them** (the images directory), in qcow2.
- While the copy runs the VM reads the backup's storage: a slow backup disk makes a slow VM until it ends.

## Verified

On a production node with libvirt 11.3, on a VM and images made for the test: the VM running from the start, the
copy ending, the new disk identical to the backup (`qemu-img compare`), no backing file left, and the backup's owner
and mode given back (libvirt had left it to `libvirt-qemu`).
