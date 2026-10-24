- A VM's devices are changed by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 2b): disks, network
  interfaces with their VLANs, PCI and USB passthrough, and CD-ROM drives. The daemon finds the volume, the network
  or the ISO itself, checks a disk image before a VM uses it, and writes each device's definition from checked fields.
  It keeps a VM's last interface. The answers are unchanged.
