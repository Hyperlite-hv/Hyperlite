# Linked clones (#423)

A VM deployed from a template can share the template's disk and store only its own changes, as Proxmox VE's linked
clones: the clone's disk is a qcow2 image whose backing file is the template's disk. It is created in an instant and
stays small; the template's disk is then shared, read-only, by every clone made from it.

Step 1 is `app/core/linked_disks.py`: create a linked disk, read a disk's backing chain
from the qcow2 header, and name the VMs that read through a given disk. The deploy option and the template's
protection come in step 2, on the structure of #546 (deploy as a task, `_deploy`) and #576 (`generated_drives`), which
change the same files.

## What was checked on real libvirt and QEMU

libvirt 10.0, QEMU 8.2 (TCG), a template made the way Hyperlite stores one (`/var/lib/hyperlite/template/vm`, created
0755 by the package's `postinst`, the file 0644 root), clones in `/var/lib/libvirt/images`, Linux 6.8 guests mounting
the disk and writing a file of their own. Each line below ran Hyperlite's own function, not a stand-in.

| What | Result |
|---|---|
| Two linked clones of one template running at the same time | Each reads the template's data and keeps its own writes; neither sees the other's; the template stays byte for byte the same |
| The template's file while clones run | libvirt gives it to QEMU's user (64055:107) and keeps it so while one clone still runs |
| The template's file once every clone stopped | **Stays owned by QEMU's user**: libvirt does not give a shared read-only backing image back to root. On Debian, AppArmor gives each QEMU read-only access to it (not checked here: no AppArmor in the lab) |
| A clone whose template is gone | Does not start: `Cannot access backing file '…/lab-tpl.qcow2'`. Hence the protection below |
| `linked_disks.chain()` against `qemu-img info --backing-chain` | The same chain |
| Hot backup (`backups.backup_hot`: external snapshot, copy, `blockCommit` + pivot) | The commit stops at the clone's own disk: the template is unchanged, the clone runs on its own disk again, still linked, no overlay left behind. The backup copy stands alone (no backing file) and holds the template's and the clone's files |
| Live disk move (`disk_move.move`: `blockCopy` full mirror, pivot) | The moved disk stands alone with everything; the VM keeps running and no longer depends on the template; the template is unchanged |
| Cold disk move (`qemu-img convert`) | The moved disk stands alone with everything |
| Growing a stopped clone's disk (`disk_resize.grow`) | The clone grows to 1 GiB while the template stays 64 MiB; it still boots and reads the template's data |
| Internal snapshot of a clone, then revert | Revert drops the clone's writes and keeps the template's data; the template is unchanged |

## What step 2 must do

- **Deploy**: a `linked` option on template deployment (off by default: API v1 only grows), creating the VM's disk
  with `linked_disks.create_linked(template_disk, new_disk)` instead of a full copy. On this host only: the template's
  disk must be where the clone runs.
- **Protect the template**: deleting or renaming a template is refused while `linked_disks.dependents()` names a VM
  (renaming moves the file the clones point to). The answer names the VMs.
- **Templates made from linked clones**: converting a linked clone into a template moves its disk, backing file
  included, so the new template depends on the old one. Either flatten the disk when converting (`qemu-img convert`,
  as a copy to a template already does), or count templates' disks among the dependents. Flattening is simpler and
  keeps every template standalone.
- **Say it**: a VM's page and the template list show which template a linked clone depends on, and how many clones a
  template has.

## Not checked yet

- **Live migration to another node** (`VIR_MIGRATE_NON_SHARED_DISK`, the destination disk pre-created as a full
  qcow2): it uses the same full mirror as the live disk move above, so the moved disk should stand alone on the
  destination, without the template. Needs two hosts.
- **Replication** (`app/core/replication.py`, `backupBegin` with checkpoints) of a linked clone.
- **AppArmor** on Debian: each QEMU's access to the shared template's disk.
