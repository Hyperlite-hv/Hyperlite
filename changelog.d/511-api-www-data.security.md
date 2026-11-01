- The HTTPS API runs as `www-data`, as Proxmox VE's pveproxy, instead of root. Everything that needs root goes through
  hyperlite-daemon, which checks each call's proof and rights, and the API reads libvirt through its read-only socket.
  A flaw in a route no longer gives root, and the node's private keys (`/etc/hyperlite/priv`) are out of its reach.
  The host's preflight check, replayed from the dashboard, no longer reports "Must be run as root".
