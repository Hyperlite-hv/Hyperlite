# LVM and LVM-thin storage pools (#417)

Proxmox VE stores VM disks on LVM (thick logical volumes) and LVM-thin (volumes that take room only as they are
written, with instant snapshots). libvirt has a `logical` pool for LVM; what it does and does not do decides what
Hyperlite does itself.

## What was checked on a real volume group

A VM of the lab (the lab's own kernel has no device-mapper): Ubuntu 24.04, Linux 6.8, its own systemd, LVM 2.03.16,
libvirt 10.0, QEMU without KVM, a second 10 GiB virtual disk for the volume group.

| What | Result |
|---|---|
| A `logical` pool on the empty disk, `build` then `create` | libvirt makes the volume group itself and starts the pool |
| A volume through libvirt | A thick logical volume, `/dev/<group>/<name>` |
| A VM with that volume as its `type='block'` disk | libvirt opens it and starts the VM; QEMU's block layer (`qemu-io`, raw) writes and reads it back |
| Resizing a volume through libvirt | **Refused**: "storage pool does not support changing of volume capacity". `lvextend` grows it; libvirt shows the new size after a pool refresh; the data is kept |
| A thin pool and an 8 GiB thin volume in a 3 GiB pool, made with `lvcreate` | LVM allows the overcommit; **libvirt lists neither** the thin volume nor the thin pool, so it cannot delete them either |
| 64 MiB written to a thin volume | The 2 GiB thin pool goes from 0 % to 3.12 %: only what is written takes room |
| A thin snapshot | Taken in about a second; it keeps the old content while the volume changes; merged back (`lvconvert --merge`, volume not in use), the volume has its old content again |
| libvirt's pool deletion (`virStoragePoolDelete`) with thin volumes in the group | **Removes the whole volume group**, the thin volumes it never listed included |
| Hyperlite's own pool removal (stop + undefine, never libvirt's deletion) on a group holding only thin volumes | libvirt calls the pool empty. Stopping it deactivates the thin volumes; defined again, their data is intact. With a thin volume in use, the stop is refused ("Logical volume … in use") |

Then Hyperlite's own code, run in that VM against the same volume group:

| What | Result |
|---|---|
| `lvm_storage.volume_groups()` and `logical_volumes()` | Read LVM's JSON reports: sizes in bytes, a thin pool's fill as a number, each volume's kind (`linear`, `thin`, `thin_pool`) |
| `lvm_storage.create_vm_disk()` with a qcow2 base image | A logical volume through libvirt, the image written into it raw (read back by `qemu-io`); a data disk is named `<vm>-2` |
| The removal guard on a group holding two volumes, a thin pool and a thin volume | Refused, naming all four; passes once the group is empty |
| `disk_resize.grow()` on a stopped VM whose disk is a logical volume | `lvextend` in MiB; libvirt shows the new size after the refresh; the data is kept. Shrinking is refused |
| `lvm_storage.delete_vm_volume()` (a deleted VM's block disks) | A volume of the logical pool is deleted through libvirt; a thin volume libvirt does not know, and a device outside any pool, are left alone |

## Design

Step 1, this change:

- **Pools**: a `lvm` pool type in pool creation: an existing volume group, or an empty disk libvirt builds one on.
- **Removal**: refused while `lvs` shows any volume in the group, thin volumes and thin pools included
  (`lvm_storage.refuse_removal_if_not_empty`). Hyperlite never calls libvirt's pool deletion.
- **Volumes**: thick ones through libvirt; the pool's volume list adds what libvirt cannot see, from `lvs`.
- **VM disks**: a logical volume made through libvirt, attached as `type='block'` with a raw driver, as ZFS zvols
  are. Growing one goes through `lvextend`.
- **VM deletion**: a block disk that libvirt knows as a volume of a logical pool is deleted with the VM, through
  libvirt; an iSCSI LUN or a volume outside such a pool is left alone.
- **VM snapshots** on thick LVM: refused with a clear message, as on iSCSI (libvirt's internal snapshots need qcow2).
- **Live migration**: refused by the migration checks (`app/core/cluster_compat.py`), as for every block disk:
  libvirt's disk copy goes through the destination pool of a file's directory, and the same group on two hosts needs
  shared storage.

Step 2: LVM-thin managed by Hyperlite (thin pools, thin VM disks, snapshots with `lvcreate -s` and rollback with
`lvconvert --merge`), all through `lvm` since libvirt sees none of it.

## Not checked yet

- A guest operating system booting from a logical volume (QEMU inside the lab's VM, both without KVM, did not reach
  the guest's init within 15 minutes; libvirt's part, opening the block device for QEMU, was seen).
- Live migration of a VM on LVM (refused for now; it needs two hosts and the same group on shared storage, or a
  block copy).
- Debian 13 and AppArmor (the lab's VM is Ubuntu 24.04, with no AppArmor).
