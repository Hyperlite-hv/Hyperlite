# Guest compatibility and hardware profiles

Hyperlite targets x86-64 KVM guests. It does not certify every operating system
that QEMU can theoretically emulate. A profile selects virtual hardware; a
compatibility claim additionally requires testing an exact OS version, host and
Hyperlite revision.

## References and design rules

- [VMware guest feature support states](https://knowledge.broadcom.com/external/article/336701)
  distinguish supported and recommended storage, networking and virtual hardware.
  A default may favor an included driver to make installation easier. Hyperlite
  follows that usability principle; VMware certification does not transfer to KVM.
- [Proxmox migration guidance](https://pve.proxmox.com/wiki/Migrate_to_Proxmox_VE)
  emphasizes matching firmware and preparing guest drivers. SATA is a fallback
  when Windows lacks VirtIO storage drivers. Hyperlite exposes SATA and VirtIO SCSI
  separately instead of treating all SCSI controllers as interchangeable.
- [Microsoft StorAHCI](https://learn.microsoft.com/en-us/windows/compatibility/storahci-replaces-msahci)
  documents the included AHCI driver in Windows 8 / Server 2012 and later.
- [Windows 11 requirements](https://www.microsoft.com/en-us/windows/windows-11-specifications)
  include UEFI, Secure Boot capability and TPM 2.0. SATA alone does not satisfy them.

## Current creation profiles

| Profile | Automatic disk / network | Firmware | Installation |
| --- | --- | --- | --- |
| Windows / Windows Server | SATA AHCI / Intel E1000e | Legacy BIOS | Interactive; extra storage driver media is unnecessary with inbox AHCI support |
| Linux | VirtIO SCSI / VirtIO | Legacy BIOS | Existing cloud-init, Kickstart and Ubuntu autoinstall flows; otherwise interactive |
| Other / generic | SATA AHCI / Intel E1000e | Legacy BIOS | Interactive; guest drivers and BIOS support must be checked |

Automatic detection reads the filename only. Recognized Windows media selects
Windows; common Linux filenames select Linux; unknown media selects Other.
An explicit profile overrides detection, including whether unattended installation
is enabled. Without installation media, the built-in Debian image and disk imports
retain their existing VirtIO defaults. An imported OS must already have the chosen
boot driver and support legacy BIOS.

The disk controller can be overridden independently: **Automatic**, **SATA**, or
**VirtIO SCSI**. Disks are presented as **Disk 1 (system)**, **Disk 2**, and so on;
libvirt device identifiers are not guest drive letters. IDE and NVMe are not
offered as system-disk choices until their complete lifecycle is implemented and
tested. Existing VMs are not converted by choosing a new creation profile.

## Validation matrix

| Guest / scenario | Current evidence | Remaining validation |
| --- | --- | --- |
| Windows Server 2025 x64, SATA | Domain validates against libvirt; the installer boots on nested KVM and DiskPart detects the 64 GiB disk without additional driver media. After WinPE network initialization, E1000e obtains a DHCP lease | Installation completion, reboot, installed-OS networking, agent, backup/restore and migration |
| Existing Linux creation flows | Automated regression tests | Repeat full installation/lifecycle checks for each distribution and release before certifying it |
| Other Windows Server releases / Windows 10 | Hardware profile available | Exact release and driver validation; no new certification claimed |
| Windows 11 | Not a supported creation profile | UEFI, Secure Boot capability, TPM 2.0 and CPU checks |
| BSD / appliances / other x86-64 OS | Generic hardware available | Installer, network, firmware and lifecycle validation for each release |
| UEFI-only imports, ARM guests, macOS | Not supported by this creation workflow | Separate architecture/firmware requirements; generic hardware is insufficient |

Unit tests cover profile selection, overrides, generated XML and subsequent disk
and NIC attachment. They do not establish that an OS has installed successfully.
Adding SATA disks requires shutting down the VM in the current implementation.

## Work required for broader compatibility

1. Introduce versioned guest profiles and host capability checks for firmware,
   CPU features, devices and resource requirements before creating disk files.
2. Add Q35/OVMF firmware selection and TPM 2.0 with packaged host dependencies.
   Firmware variables and TPM state must be preserved in backup/restore, clone,
   snapshots, migration and deletion; a boot-only prototype is insufficient.
3. Validate each supported OS version through installation, boot without its ISO,
   disk/network I/O, shutdown/reboot, additional devices, snapshot restore,
   backup restore and migration where available. Record exact image hashes,
   hypervisor/host versions and results without redistributing installation media.
4. Promote profiles from available to validated only with that evidence. Retain
   simple defaults and show advanced settings only when users need an override.
