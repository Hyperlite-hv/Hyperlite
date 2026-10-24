- Backup stores, as Proxmox Backup Server's datastores (#337, #420; `docs/design/backup-store.md`): a store lives in
  a directory of a storage pool (directory, NFS, SMB) and keeps every VM's disks as 4 MiB chunks named by their
  digest, each held once whatever the number of VMs and backups that share it; a backup is a complete image, never a
  chain. VMs with ZFS, LVM or iSCSI disks can be backed up into a store, running or stopped; a running VM's disks are
  read as they were when the backup began, while it keeps writing. Restores over the VM (file disks swapped in at
  once, block devices written in place) or to a new VM; verification, retention, deletion as for every backup, and a
  weekly garbage collection, one node at a time in a cluster. Through the API for now (`/backup-stores`, `store` on
  backups, schedules and grouped jobs); the dashboard comes next.
