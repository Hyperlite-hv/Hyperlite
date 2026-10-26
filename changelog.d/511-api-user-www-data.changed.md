- The API (hyperlite.service) runs as www-data, as Proxmox VE's pveproxy: what changes the node is done by
  hyperlite-daemon, which checks the account behind each request.
