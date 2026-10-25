- The scheduled work runs in a service of its own, `hyperlite-scheduler`, as Proxmox VE's `pvescheduler` (#511,
  step 4c): the scheduled backups (with the backup groups and the stores' copies and garbage collections), the
  replication, the automatic deletion of inactive VMs, the update check and the start at boot. It starts and restarts
  with the API, keeps running if the API crashes, and its tasks appear and can be cancelled in the dashboard as
  before. Its log: `journalctl -u hyperlite-scheduler`.
