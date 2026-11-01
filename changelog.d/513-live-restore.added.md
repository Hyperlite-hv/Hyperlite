- Live restore (#513): a backup kept as files is restored as a new VM that starts at once, its disks copied from the
  backup while it runs, as Proxmox VE's. A copy that fails removes the new VM; the backup is never written. **Live
  restore** in a VM's backups, or `"live": true` on `POST /backups/{id}/restore`. Not yet from a backup store.
