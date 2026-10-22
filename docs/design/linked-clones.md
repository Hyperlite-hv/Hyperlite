# Linked clones (#423)

A VM deployed from a template can share the template's disk and store only its own changes, as Proxmox VE's linked
clones: the clone's disk is a qcow2 image whose backing file is the template's disk. It is created in an instant and
stays small; the template's disk is then shared, read-only, by every clone made from it.

Step 1 is `app/core/linked_disks.py`: create a linked disk, read a disk's backing chain from the qcow2 header, and
name the VMs that read through a given disk. Step 2 uses it in `app/routers/templates.py` and the templates page:

- **Deploy**: `POST /templates/{name}/deploy` takes `linked` (off by default: API v1 only grows). The new VM's disk is
  then `linked_disks.create_linked(template_disk, new_disk)` instead of a full copy, on this host (the template's disk
  must be where the clone runs). A template whose disk is not qcow2 is only copied (422); `GET /templates` says it
  with `linkable`.
- **Protect the template**: deleting or renaming a template is refused (409) while `linked_disks.dependents()` names a
  VM; renaming moves the file the clones point to. The answer names the VMs. When libvirt does not answer, nothing can
  be checked and the request is refused (503).
- **Templates made from linked clones**: converting a linked clone into a template flattens its disk
  (`qemu-img convert` into a partial file next to the templates, renamed once the VM is undefined), so every template
  stands alone and the protection only has VMs to count.
- **Say it**: `GET /templates` adds `linked_clones`, the VMs reading through each template (`null` when libvirt does
  not answer); the templates page shows them and offers "linked clone" when deploying a template whose disk is qcow2.

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

### Step 2 through the API

The same libvirt and QEMU, Hyperlite's API running against them (`POST /templates/from-vm`, `/deploy`, `DELETE`,
`/rename`), each VM booted and its file read from inside the guest:

| What | Result |
|---|---|
| A linked and a full deployment of one template | Once created, the linked disk is 196,616 bytes, the full copy 655,360; both boot and read the template's file |
| `GET /templates` | Names the linked clone, not the full copy |
| Delete or rename the template while the clone exists | 409, naming the clone; the template's files are unchanged |
| A linked clone converted into a template | Its new template's disk has no backing file and holds the old template's data and the clone's writes; a VM deployed from it reads both |
| Delete the template once its clones are gone | 200 |
| The full copy, its template deleted | Still boots and reads its data |

## Not checked yet

- **Live migration to another node** (`VIR_MIGRATE_NON_SHARED_DISK`, the destination disk pre-created as a full
  qcow2): it uses the same full mirror as the live disk move above, so the moved disk should stand alone on the
  destination, without the template. Needs two hosts.
- **Replication** (`app/core/replication.py`, `backupBegin` with checkpoints) of a linked clone.
- **AppArmor** on Debian: each QEMU's access to the shared template's disk.
