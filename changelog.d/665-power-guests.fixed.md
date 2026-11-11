- Rebooting or powering off a node: a libvirt error while waiting for the guests to stop (libvirtd restarting, the
  connection lost) no longer reads as "stopped", which could take the node down with VMs still running. The guest
  counts as running and the connection is opened again (#665).
- The reboot or power-off is audited with its outcome (done, or cancelled because guests kept running) instead of
  "success" at the request (#665).
