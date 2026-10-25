- The permissions are written by `hyperlite-daemon` only (#511, step 4b), as `pvedaemon` is the only writer of
  Proxmox VE's `user.cfg` and `priv/`. This covers:
  - the accounts, the API tokens, the security keys, the second factors and the sign-outs;
  - the groups, the custom roles, the resource pools and the ACLs, including their renames and those of the VMs and
    containers they name.

  The API asks the daemon for each change and no longer writes these tables itself. A second factor's secret is made,
  sealed and checked by the daemon. In a cluster, these changes still answer once the cluster took them.
  `hyperlite.service` now starts after `hyperlite-daemon.service`, which tells systemd when its socket answers.
