- A Linux bridge can be made VLAN-aware from the dashboard (#345), as Proxmox VE's "VLAN aware" bridge: in a bridge
  network's details, **Make the bridge VLAN-aware** turns VLAN filtering on with VLANs 2 to 4094 tagged on its uplink,
  untagged traffic unchanged. The change is a trial the dashboard confirms from a new request a few seconds later; if
  the host can no longer be reached, it undoes the change by itself within 60 seconds. Kept, it is set again at every
  boot, before libvirt, by `hyperlite-vlan-bridges.service`, whatever manages the host's network. It can be turned off
  again while no VM keeps VLANs on the bridge (`POST`/`DELETE /networks/{name}/vlan-aware`).
