- The claims that keep one operation at a time on a VM or a container (a backup, a restore, a snapshot, a migration)
  are files of `/run/lock/hyperlite`, locked by the process that works on the guest, as Proxmox VE locks a guest with
  `/run/lock/qemu-server` (#511, step 4c). Every Hyperlite process sees them: once the scheduled backups and
  replication run in a service of their own, an operation the API is asked for is still refused while one of them
  works on that guest. A process that stops, even killed, leaves no claim behind. `HYPERLITE_LOCK_DIR` moves the
  directory for development and tests.
