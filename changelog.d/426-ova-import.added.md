- A VM exported from VMware as an OVA can be imported (#426): upload the `.ova` with the imported disks, then **Create
  a VM** on its row (Library > Imported disks). Its settings are read from the OVF descriptor (vCPUs, memory, disks,
  BIOS, UEFI or UEFI with Secure Boot, the system's family, network cards), its disks copied out of the archive and
  checked against its manifest, and the VM created from them, stopped (`GET /vm-disks/{file}/ovf`,
  `POST /vm-disks/{file}/extract`, `disks[].import_disk` in `POST /vms`). A live connection to an ESXi host is not
  there yet.
