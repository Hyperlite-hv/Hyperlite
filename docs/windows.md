# Installing Windows guests

## New VM

1. Upload the Windows Server installation ISO under **Storage**.
2. Select it in **Create VM**. Hyperlite detects common Windows ISO filenames.
   For a renamed ISO, select **Windows / Windows Server** under **Operating system**.
3. Set the name and review resources. Selecting Windows raises the suggested
   minimums to 2 vCPUs, 4 GiB RAM and a 64 GB system disk; adjust resources for
   your workload and Windows edition.
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

## UEFI, Secure Boot and TPM 2.0 (Windows 11)

Windows 11 requires UEFI firmware with Secure Boot and a TPM 2.0. In **Create VM →
Advanced**, **Firmware** offers:

| Choice | What the VM gets |
| --- | --- |
| BIOS (legacy) | The historical `pc` machine with SeaBIOS; every VM created before this option |
| UEFI | The `q35` machine with OVMF, Secure Boot off |
| UEFI + Secure Boot + TPM 2.0 | `q35`, OVMF with the Microsoft keys enrolled, SMM, and a software TPM 2.0 (`swtpm`) |

**Automatic** picks UEFI + Secure Boot + TPM 2.0 for a Windows installation when the
host can build it, and BIOS otherwise. The host needs the `ovmf`, `swtpm` and
`swtpm-tools` packages (recommended by the Hyperlite package); when one is missing
the choice is disabled and the wizard names the package. `GET /host/firmware`
returns the same answer.

At the first start, open the console quickly: the Windows DVD asks to *press any key
to boot from CD or DVD* for a few seconds only. If it was missed, reset the VM.

What differs for a UEFI VM:

- **Drives**: `q35` has no IDE bus, so the CD drives are SATA (`sdw` for the
  installation DVD, `sdz` for the drivers CD). The API and the Hardware page keep
  addressing them by their slot (`hda`, `hdd`).
- **Snapshots** are taken while the VM is stopped: QEMU cannot save the state of a
  running VM whose firmware runs from flash. The Snapshots page says so and the API
  answers 409 instead of failing later.
- **Deletion** also deletes the VM's NVRAM (UEFI variables) and TPM state.
- **Clone and template deployment** give the new VM a fresh NVRAM and TPM. It boots
  through the UEFI fallback path, which Windows installs; anything sealed in the
  source's TPM (BitLocker keys, Windows Hello) is not carried over.
- **Backups** copy the NVRAM and the TPM state with the disks, and a restore puts them
  back (in place, or under the new VM's name and UUID), so BitLocker and the boot
  entries survive. Keep the BitLocker recovery key outside the VM anyway.
- **Live migration**: QEMU carries the NVRAM and TPM state in the migration stream;
  it still has to be validated on real hosts before it is relied on.

## Hyperlite Tools

Hyperlite Tools is the QEMU guest agent. On Windows it comes with the
[virtio-win guest tools](https://virtio-win.github.io/Knowledge-Base/Driver-installation.html):
mount the virtio-win ISO (**Hardware → Windows drivers CD**) and run
`virtio-win-guest-tools.exe`, or install `guest-agent\qemu-ga-x86_64.msi` alone.
The VM summary then shows **Hyperlite Tools: Running**, shutdowns and reboots go
through the agent, and the IP address is reported by Windows itself.

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
Hyperlite does not supply Windows licenses or installation media. UEFI, Secure Boot
and TPM 2.0 are available (see above); Windows 11 also checks the CPU model, which
the host decides.
Guest installation and networking must be validated with the selected Windows media
on a suitable KVM host; XML/unit tests alone do not establish guest compatibility.

See the [compatibility matrix](guest-compatibility.md) for other guest families and
the remaining work toward broader OS support.

The API accepts `guest_os` (`auto`, `windows`, `linux`, `other`; default `auto`) and optional
`drivers_iso` alongside the installation `iso`. Automatic detection uses the filename,
not the contents of the ISO. Use an explicit profile for ambiguous names.
`disk_controller` accepts `auto`, `sata` or `virtio-scsi`; default `auto` follows
the guest profile. An explicit controller also applies to disk imports.
