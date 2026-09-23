# Installing Windows Server guests

## New VM

1. Upload the Windows Server installation ISO under **Storage**.
2. Select it in **Create VM**. Hyperlite detects common Windows ISO filenames.
   For a renamed ISO, select **Windows / Windows Server** under **Operating system**.
3. Set the name and review resources. Selecting Windows raises the suggested
   minimums to 2 vCPUs, 4 GiB RAM and a 64 GB system disk. Host allocation limits
   still apply; adjust resources for your workload and Windows edition.
4. Create and start the VM. Open the console, press a key to boot the DVD if
   requested, and follow Windows Setup normally.

The Windows profile uses a SATA (AHCI) disk controller so Windows Setup does not
need a separate storage driver ISO. It uses an emulated Intel `e1000e` network
adapter. Linux guests keep VirtIO SCSI and VirtIO networking. The choice is applied
at creation; existing guests and imported disks are not automatically converted.

Disks appear as **Disk 1 (system)**, **Disk 2**, etc. in the wizard. The separate
**Disk controller** selector offers **Automatic**, **SATA** and **VirtIO SCSI**.
Automatic selects SATA for Windows and generic installation media, and VirtIO SCSI for Linux.
Overriding it to VirtIO SCSI for Windows requires loading the corresponding driver
during Setup. Hyperlite's VirtIO SCSI is not the same controller as VMware SCSI.

Additional driver media is available under the collapsed **Advanced: additional
drivers** section. It is optional for SATA storage. Changing a running installation's
disk controller requires preparing the appropriate boot driver first; installing
guest tools alone does not change the VM's hardware.

## Existing VM with VirtIO SCSI disks

If an older Windows VM has no visible disk in Setup, obtain compatible signed
drivers from the [VirtIO project](https://virtio-win.github.io/Knowledge-Base/Driver-installation.html).
Upload its ISO in **Storage**, shut down the VM and mount it using **Hardware →
Windows drivers CD (hdd) → Mount drivers**. Start the VM and choose **Load driver**
in Windows Setup, then browse to `vioscsi`, the matching Windows version and `amd64`.
The Windows installation DVD remains in its own drive. Ejecting the driver ISO
affects only `hdd`.

## Scope

This simplifies virtual hardware selection; Windows Setup remains interactive.
Hyperlite does not supply Windows licenses or installation media. The profile does
not configure UEFI, Secure Boot or a TPM, and is not a complete Windows 11 profile.
Guest installation and networking must be validated with the selected Windows media
on a suitable KVM host; XML/unit tests alone do not establish guest compatibility.

See the [compatibility matrix](guest-compatibility.md) for other guest families and
the remaining work toward broader OS support.

The API accepts `guest_os` (`auto`, `windows`, `linux`, `other`; default `auto`) and optional
`drivers_iso` alongside the installation `iso`. Automatic detection uses the filename,
not the contents of the ISO. Use an explicit profile for ambiguous names.
`disk_controller` accepts `auto`, `sata` or `virtio-scsi`; default `auto` follows
the guest profile. An explicit controller also applies to disk imports.
