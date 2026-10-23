- VLANs on VLAN-aware Linux bridges (#345), as Proxmox VE's `tag` and `trunks`: on a bridge network whose bridge
  filters VLANs, a VM interface takes an access VLAN (untagged inside the VM) and trunk VLANs it sees tagged
  (`10,20-30`). libvirt refuses a VLAN on a Linux bridge, so Hyperlite records it in the VM's definition and sets it on
  the VM's bridge port: when the VM starts or arrives by migration (a libvirt hook the package installs, which restarts
  libvirt once; running VMs keep running), when it is changed on a running VM, and when the service starts. A port
  whose VLAN cannot be set carries no traffic rather than the bridge's untagged one, and says why in the journal
  (`journalctl -t hyperlite-vlan`). The network list tells which networks take VLANs and how (`vlan_mode`).
