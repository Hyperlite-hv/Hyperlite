- Copies of another cluster's backup stores (#420, step 4, part 2), as Proxmox Backup Server's remotes: the backup site
  pulls a store of the other site over its API, its HTTPS certificate pinned, with the API token of a dedicated account
  there that may only read that store (the new privilege `backup_store.read`, the role "Backup store reader"). The
  copy of an encrypted store keeps no key at the backup site until it is restored from: the administrator unlocks it
  then with the key's code (`/backup-remotes`, `copy_of: "<remote>:<store>"`, `POST /backup-stores/{name}/unlock`).
