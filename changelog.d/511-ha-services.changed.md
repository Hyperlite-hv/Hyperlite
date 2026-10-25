- HA runs in two services of its own, as Proxmox VE's `pve-ha-lrm` and `pve-ha-crm` (#511, step 4c, #333):
  - `hyperlite-ha-lrm`, on every node: the watchdog, the monitoring of the node's HA VMs, the node's heartbeat and
    the load-balancing moves asked of it;
  - `hyperlite-ha-crm`, active on the leader: the recovery of a lost member's HA VMs and load balancing's evaluation.

  A hang or a crash of the API or of the cluster's decisions no longer stops the watchdog's feeding, so a healthy node
  is no longer reset because of them. A dead LRM still stops both the feeding and the heartbeat, so a member's VMs are
  never restarted elsewhere while they still run. The HA and load-balancing pages show the same state as before.
  Logs: `journalctl -u hyperlite-ha-lrm` and `-u hyperlite-ha-crm`.
