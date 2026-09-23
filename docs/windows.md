# Installing Windows guests

Hyperlite presents VM disks through a **VirtIO SCSI** controller and the network
adapter through VirtIO. Windows installation media does not normally include these
drivers: an empty disk selection screen does not mean that the virtual disk is missing.

## New VM (including Windows Server 2025)

1. Obtain your Windows installation ISO and a recent signed VirtIO Windows driver
   ISO from the [VirtIO project](https://virtio-win.github.io/Knowledge-Base/Driver-installation.html).
   Use a release supporting your Windows version; old driver media may not support Server 2025.
2. Upload the Windows ISO in **Storage**. In the VM wizard select it as the installation ISO.
3. Upload the driver ISO using the upload control below **Windows / additional drivers ISO**,
   then select it in that dropdown. The upload does not select the ISO automatically.
4. Allocate sufficient CPU, memory and disk space for your Windows edition. Create and
   start the VM, open the VNC console, and press a key to boot the Windows DVD when prompted.
5. At the Windows disk selection screen, choose **Load driver**, browse the separate
   VirtIO CD and select **vioscsi**, your Windows version and **amd64**. Keep the option
   to hide incompatible drivers enabled. The controller is VirtIO SCSI, so use
   `vioscsi`, not `viostor` (which is for VirtIO block disks).
6. Once the disk appears, continue the installation. Both ISOs remain mounted; after
   the first reboot, let Windows boot from its system disk rather than pressing a key
   to restart the DVD installer.
7. In Windows, run the VirtIO guest-tools installer from the driver CD to install the
   network driver (NetKVM) and other guest drivers. Reboot if requested.

The driver CD is a read-only IDE device (`hdd`), which Windows Setup can read before
loading VirtIO drivers. It has no boot priority. The installation DVD remains `hda`.
Existing Linux creation and unattended-installation behavior is unchanged.

## An existing VM is stuck at disk selection

Upload the VirtIO ISO under **Storage**. In the VM's **Hardware** tab, select it under
**Windows drivers CD (hdd)** and click **Mount drivers**. If that CD drive does not
exist yet, shut down the VM before adding it, then start the VM again. Existing CD
drives support media changes while running. Load `vioscsi` in Windows Setup as above.
This does not recreate, format or change the disk controller of the VM.

**Eject drivers** removes only the media in `hdd`, leaving the installation DVD alone.

## Scope and verification

This feature supplies driver media; it does not automate Windows Setup, provide a
Windows license, or configure UEFI, Secure Boot or a TPM. Check the firmware and
hardware requirements of your chosen Windows edition separately (especially Windows 11).
Driver loading and a full installation must be verified on a suitable KVM host;
unit tests of domain XML alone do not establish Windows compatibility.

The creation API accepts optional `drivers_iso` alongside `iso` (uploaded filenames).
The existing CD-ROM endpoints accept an optional IDE `target_dev`: in the PUT body
for mounting, or as a DELETE query parameter for ejection. Omitting it preserves
the previous first-CD behavior.
