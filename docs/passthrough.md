# Host device passthrough

A VM can use a physical device of its server on its own: a USB device (a licence dongle, a Zigbee stick, a smart
card reader) or a PCI card (a GPU, a network card for a router VM, a disk controller for a storage VM). Proxmox calls
it *PCI/USB passthrough*, vSphere *DirectPath I/O* and *USB passthrough*.

In the dashboard: **VM → Hardware → Host devices → Give a host device** (administrators only).

## USB

Nothing to prepare. The device is matched by its vendor and product id, so it follows the VM when it is unplugged
and plugged into another port; when two identical devices are connected, by their bus and address instead. A USB
device can be given to, or taken back from, a running VM.

## PCI

1. **Firmware**: enable VT-d (Intel) or AMD-Vi / IOMMU (AMD) in the server's BIOS/UEFI setup.
2. **Kernel**: add `intel_iommu=on iommu=pt` (Intel) or `amd_iommu=on iommu=pt` (AMD) to the kernel command line.
   On Debian: edit `GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, run `update-grub`, reboot.
3. Check: `ls /sys/kernel/iommu_groups` lists numbered directories. The **Give a host device** drawer says when the
   IOMMU is off.
4. Shut the VM down, give it the card, and start it. While the VM runs, libvirt unbinds the card from its host driver
   (VFIO); it returns to the host when the VM stops.

A card shares an **IOMMU group** with the devices the hardware cannot isolate from it (a GPU and its HDMI audio
function, typically): the whole group is given together, and it is refused if one of its devices is used by the
host or by another VM.

### What is never offered

- the graphics card that shows the host console (`boot_vga`);
- a network card with an IP address or enslaved to a bridge or bond: the way into the server;
- a controller holding a mounted file system, swap or a ZFS pool member;
- PCI bridges.

The check reads the host at the moment of the request. A card that the host starts using later (a new bridge member,
say) is still taken when the VM starts: keep management and VM cards apart.

## Limits

- A VM with a host device cannot be live-migrated: the device is in this server. An HA recovery on another node cannot bring the device along; keep such VMs out of HA or expect them to fail to start elsewhere.
- GPU passthrough may need more depending on the card (a q35 machine, the card's ROM, vendor-reset for some AMD
  cards). Hyperlite passes the device; the guest driver does the rest.
- SR-IOV virtual functions and mediated devices (vGPU) are not offered yet.
