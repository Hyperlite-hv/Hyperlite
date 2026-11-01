- `/etc/hyperlite` is the cluster's configuration (#525, step 3), as `/etc/pve` on Proxmox VE: hyperlite-cfs mounts its
  tree there read-only, the node's certificate and keys move into it at the first start (under `nodes/<host>/`, read
  at `/etc/hyperlite/local/...`, the node's secrets at `/etc/hyperlite/local/priv/...`), and the permissions database
  moves to `/var/lib/hyperlite/access/`. The directory as it was is kept in
  `/var/lib/hyperlite/etc-before-cfs-<date>`. hyperlite-cfs now runs on every node, and the services start after it.
  A node joining a cluster keeps its files; a member removed loses its own. Installs only over 1.13.1 or later, which
  puts the files back if this version is rolled back.
