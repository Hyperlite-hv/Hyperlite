- A VM is deleted by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 3b), with Proxmox VE's rule
  for its disks rather than vSphere's. Its own disks and the drives Hyperlite made for it go. A disk another VM uses,
  a disk another VM's linked disk reads through, or a file outside every storage pool of the node is now kept: vSphere
  deletes a disk shared with another VM, and a file attached by hand is not Hyperlite's to delete. The answer lists
  what was kept and why (`kept_disks`), as do the audit log, the automatic clean-up's entry and the dashboard.
