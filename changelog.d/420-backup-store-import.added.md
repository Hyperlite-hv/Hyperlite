- A backup of today's format can be copied into a backup store (#420, step 4, part 3): **Into a store…** in a VM's
  backups, or `POST /backups/{id}/import`. It is verified first, its disks are read whole (a replication point with its
  chain) and cut into the store's chunks, only what the store lacks written; the backup itself stays as it is.
