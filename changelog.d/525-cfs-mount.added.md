- The cluster's replicated configuration reads as files at `/etc/hyperlite/cfs`, as Proxmox VE's `/etc/pve`: one
  JSON file per object, `priv/` root's alone, every write refused (changes go through the API), and `.members`,
  `.version` and `.quorum` made when read. A change made on any node shows at once. Needs the `fuse3` package, now a
  dependency.
