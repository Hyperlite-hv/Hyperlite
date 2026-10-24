- A VM's snapshots are taken, restored and deleted by `hyperlite-daemon` instead of the API (#511, step 3, VM domain
  part 4): libvirt's internal snapshots, ZFS snapshots of every zvol and LVM-thin snapshots alike. The daemon checks
  the name, the VM's state and the snapshot again, and builds a snapshot's definition itself. An LVM-thin VM's list
  is read through it, since `lvm` needs root. The answers are unchanged. An unexpected failure is no longer described
  in a task's error; the daemon's log has it.
