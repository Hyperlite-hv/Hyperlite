- `hyperlite-admin`, the administration CLI of a node (#516), as `qm`, `pct` and `pvesh` on Proxmox VE: any route of
  API v1 (`hyperlite-admin get /vms`), short commands for VMs, containers, storage, networks, backups, tasks, the
  cluster and the node (`hyperlite-admin vm migrate web1 node3`), the parameters of a route (`hyperlite-admin api`),
  `--json` for scripts and the tasks waited for with their log. As root it needs no token: the node signs it in as
  `root@<node>`, an administrator that has no password and no way in from the web, under which the audit log records
  its actions on every member. Deletions, restores and updates ask first, or take `--yes`.
