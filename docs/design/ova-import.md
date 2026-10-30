# Importing a VM exported from VMware (#426)

Proxmox VE imports VMs from an ESXi host with a wizard. Antho decided on two steps:

1. **An OVA exported from VMware** (ESXi, vCenter, Workstation, ovftool), this change.
2. **A live connection to an ESXi or a vCenter**, listing its VMs and importing one: only once an ESXi is available to
   test it against, which there is not today.

## What an OVA holds

A tar archive: the OVF descriptor (XML) first, an optional manifest (`.mf`, SHA1/256/512 of each file), then the disks
(usually streamOptimized vmdk). The descriptor names the VM's virtual hardware as CIM resource items.

## Mapping (`app/core/ova.py`)

| OVF | Hyperlite |
|---|---|
| `VirtualSystem/Name` | VM name (what a name refuses becomes a dash, in the dashboard) |
| Item ResourceType 3, `VirtualQuantity` | vCPUs |
| Item ResourceType 4, `VirtualQuantity` × `AllocationUnits` (`byte * 2^20`, `byte * 2^30`, `MegaBytes`…) | memory |
| Item ResourceType 17 → `DiskSection/Disk` → `References/File` | disks, in that order, with their capacity |
| `vmw:Config` `firmware=efi`, with `uefi.secureBoot.enabled=true` | firmware `uefi`, or `uefi_secure` |
| `OperatingSystemSection` `vmw:osType` and description | guest family: windows, linux, other |
| Item ResourceType 10, `Connection` and `ResourceSubType` | network cards: the first goes on the chosen network |

The guest family sets the devices, as at creation: SATA disks and an e1000e card for Windows and other systems,
VirtIO SCSI and VirtIO for Linux. For an imported disk this now follows the family asked for (an explicit `guest_os`):
the system on the disk is not Hyperlite's, and a Windows guest without VirtIO drivers does not boot from a VirtIO
disk. Without one, imports keep the Linux devices they always had.

## The flow

1. The OVA is uploaded as an imported disk (`POST /vm-disks`, `.ova` accepted).
2. `GET /vm-disks/{file}/ovf` gives the mapped settings, and the name each disk takes once extracted.
3. `POST /vm-disks/{file}/extract` (`?wait=false`: a task) copies the disks out as imported disks
   (`<ova>-disk<N>.vmdk`), each checked against the manifest and its size; a failure removes what was copied.
4. `POST /vms` creates the VM: `import_disk` for the first disk, `disks[i].import_disk` for the others (every storage
   kind but iSCSI, whose LUNs take no file).

The dashboard does 2 to 4 from Library > Imported disks > **Create a VM** on an OVA's row.

## What a hostile file is refused for

The archive comes from a user: only plain members with plain names are read (no link, no device, no path), and
`tarfile` never extracts anything itself. The descriptor is refused with a DTD (an OVF never has one; entity
expansion is how XML files blow up). Disk names come from the descriptor and must name members of the archive.
Compressed or chunked disk files, several VMs in one OVA (a vApp) and malformed numbers are refused with a message.

## Checked

- Ubuntu's official OVA (`noble-server-cloudimg-amd64.ova`, made with VMware's OVF tooling): read as 2 vCPUs,
  1024 MiB, BIOS, Linux, one 10 GiB disk, a VmxNet3 card on "VM Network"; its disk extracted in 4.9 s, matching the
  manifest's SHA256; converted with `qemu-img convert -O qcow2` as Hyperlite does, it boots under QEMU to the
  `ubuntu login:` prompt.
- Unit and API tests on descriptors written as ESXi 7 and ovftool write them: a Windows Server UEFI + Secure Boot VM
  with two disks and two cards, the refusals above, the manifest check, the tasks, a VM made from several imported
  disks, the device models of an imported Windows guest.

Not checked: a VM created through the API from an OVA on a real host (this sandbox cannot create a network bridge),
and a Windows or UEFI OVA exported from a real ESXi.
