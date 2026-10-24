- A node registered over SSH only is changed by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain
  part 2). This covers its VMs' power, pre-stop script, start at boot, libvirt autostart flag, guest agents, rename,
  hook script and deletion (with Hyperlite's rule on the disks it keeps), its storage pools, LVM report, default pool
  and its host's details. The daemon opens that node's libvirt with the cluster's key, which only it reads. The API
  reads the node through a read-only tunnel the daemon opens to the node's read-only libvirt socket
  (`/run/hyperlite/nodes`). The answers are unchanged. Migration and HA still use the API's connection until they
  move.
