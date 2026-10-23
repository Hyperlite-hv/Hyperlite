- LVM-thin storage (#417): an LVM pool can name a thin pool, an existing one of its volume group or one Hyperlite
  creates over its free space. VMs created there get thin disks, which take room only as they are written, and
  snapshots: disk only, taken in about a second per disk (a running VM with several disks is paused meanwhile),
  restored with the VM stopped, and kept after a restore, so a VM can go back to the same snapshot again. A deleted
  VM takes its thin disks and their snapshots with it. The pool list shows the thin pool's size and fill.
