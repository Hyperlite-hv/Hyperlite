- The network firewall's iptables rules and the VLANs of Linux bridges are set by `hyperlite-daemon` instead of the API
  (#511, step 3, network domain part 1, `app/daemon/ops/network.py`): applying or removing a network's firewall, the
  trial, confirmation and undoing of a VLAN-aware bridge, the VLANs of a running VM's ports, and their replay when the
  service starts. The daemon is asked by a network's or a VM's name and reads the bridge and the ports itself, so the
  API cannot point them at another interface; the API keeps who may ask, the audit log and the firewall's rules.
  Without the daemon, these answer that it is unavailable (503).
