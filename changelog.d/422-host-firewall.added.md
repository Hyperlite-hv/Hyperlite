- A firewall for the nodes themselves (Datacenter › Firewall), as Proxmox VE's datacenter and node firewalls: input
  rules for the whole cluster and for each node, security groups and IP sets, applied with nftables in a table of
  Hyperlite's own. A change is checked by nftables, applied on the node first and must be confirmed from the page
  within its delay, otherwise the previous rules come back; once confirmed, every member applies it. The cluster's
  members, established connections, ICMPv6 and the VMs' DHCP and DNS are always let in. Off until turned on.
