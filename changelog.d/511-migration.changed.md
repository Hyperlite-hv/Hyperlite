- A live migration is done by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain part 3). The daemon
  opens both nodes' libvirt with the cluster's key, prepares the destination (its networks, its default pool, the
  VM's addresses, the disks and CD-ROM files) and runs the migration, writing its progress into the task. A
  cancellation aborts libvirt's job. The API then writes where the VM runs (HA protection first, tags and notes,
  start at boot) and asks the daemon to clean the source up. The daemon kept what to clean, so the API never names a
  file to delete. The answers are unchanged.
