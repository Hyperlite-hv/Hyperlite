"""VM firmware: legacy BIOS (SeaBIOS), UEFI (OVMF), or UEFI with Secure Boot and a TPM 2.0 (what Windows 11 needs).

Wire values of the `firmware` field: "bios" (the default, every VM created before this option), "uefi" and
"uefi_secure".

A UEFI VM uses the q35 machine: Secure Boot needs SMM, which libvirt only offers on q35, and plain UEFI takes the
same machine so both UEFI choices behave alike. q35 has no IDE bus, so its CD-ROM drives are SATA (see
cdrom_target()). libvirt picks the OVMF image itself from the firmware descriptors installed with the `ovmf` package
(`<os firmware='efi'>` plus the wanted features), and creates the VM's NVRAM (its UEFI variables: boot entries,
enrolled Secure Boot keys) from that image's template. The TPM is software, emulated by `swtpm`, its state kept by
libvirt under /var/lib/libvirt/swtpm/<uuid>.

What changes for such a VM, and is handled where it happens:
  - deleting it also deletes its NVRAM and TPM state (undefine_flags());
  - a clone or a template deployment gets a fresh NVRAM and TPM (drop_nvram() and a new UUID), never the source's
    files: two VMs writing the same NVRAM would corrupt it;
  - QEMU cannot take an internal snapshot of a running VM whose firmware is in pflash, so snapshots of a running
    UEFI VM are refused with a clear message (stop it first);
  - backups copy the disks only, not the NVRAM or the TPM state: a restored VM boots through the UEFI fallback
    path, and anything sealed in the TPM (BitLocker keys) is gone. Documented in docs/windows.md.
"""

import logging
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)

BIOS = "bios"
UEFI = "uefi"
UEFI_SECURE = "uefi_secure"
CHOICES = (BIOS, UEFI, UEFI_SECURE)

Q35 = "q35"
# SATA drives beyond the disks: the disks take sda, sdb... on the same bus when the VM uses SATA disks, so the
# drives that used to be IDE hda..hdd take the last letters. The installation ISO keeps the lowest one of them,
# so it is still the first CD-ROM drive of the system (the kickstart `cdrom` directive depends on it, see
# vm_builder.build_domain_xml).
_Q35_TARGETS = {"hda": "sdw", "hdb": "sdx", "hdc": "sdy", "hdd": "sdz"}


def cdrom_target(ide_dev, firmware):
    """(target dev, bus) for one of the historical IDE drive slots (hda..hdd) on a VM with this firmware."""
    if firmware == BIOS:
        return ide_dev, "ide"
    return _Q35_TARGETS[ide_dev], "sata"


def os_xml(firmware, os_extra_xml=""):
    """The <os> element (and, for UEFI, the firmware features libvirt selects OVMF with)."""
    if firmware == BIOS:
        return f"""<os>
    <type arch='x86_64' machine='pc'>hvm</type>{os_extra_xml}
  </os>"""
    secure = "yes" if firmware == UEFI_SECURE else "no"
    # enrolled-keys: the Microsoft keys are already enrolled in the NVRAM template, so Windows (and the distributions
    # booting through the Microsoft-signed shim) boot with Secure Boot on, with no key to enroll by hand. Asking for
    # it only with secure-boot: libvirt finds no descriptor for "keys enrolled, Secure Boot off".
    keys = "\n      <feature enabled='yes' name='enrolled-keys'/>" if firmware == UEFI_SECURE else ""
    return f"""<os firmware='efi'>
    <type arch='x86_64' machine='{Q35}'>hvm</type>
    <firmware>
      <feature enabled='{secure}' name='secure-boot'/>{keys}
    </firmware>{os_extra_xml}
  </os>"""


def features_xml(firmware):
    # SMM keeps the Secure Boot variables out of the guest OS's reach; OVMF's Secure Boot build refuses to run
    # without it.
    smm = "\n    <smm state='on'/>" if firmware == UEFI_SECURE else ""
    return f"""<features>
    <acpi/>
    <apic/>{smm}
  </features>"""


def tpm_xml(firmware):
    if firmware != UEFI_SECURE:
        return ""
    return """
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>"""


def of_domain(root):
    """The firmware of a parsed domain XML: BIOS, UEFI or UEFI_SECURE."""
    os_el = root.find("os")
    if os_el is None:
        return BIOS
    loader = os_el.find("loader")
    if os_el.get("firmware") != "efi" and (loader is None or loader.get("type") != "pflash"):
        return BIOS
    secure = (loader is not None and loader.get("secure") == "yes") or any(
        f.get("name") == "secure-boot" and f.get("enabled") == "yes" for f in os_el.findall("firmware/feature")
    )
    return UEFI_SECURE if secure else UEFI


def has_tpm(root):
    return root.find("./devices/tpm") is not None


def is_pflash(root):
    """True when QEMU runs the firmware from pflash (every UEFI VM): no internal snapshot while it runs."""
    return of_domain(root) != BIOS


def undefine_flags(root):
    """Extra undefine flags a VM needs so its NVRAM and TPM state go with it. Without the NVRAM flag, libvirt
    refuses to undefine a UEFI VM ("cannot undefine domain with nvram"). Only added when the VM has them, so the
    undefine of a BIOS VM is exactly what it was, also on a libvirt older than the TPM flag (8.9)."""
    flags = 0
    if of_domain(root) != BIOS:
        flags |= libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
    if root.find("./devices/tpm/backend[@type='emulator']") is not None:
        flags |= libvirt.VIR_DOMAIN_UNDEFINE_TPM
    return flags


def drop_nvram(root):
    """Remove the NVRAM path from a domain XML about to be defined under another name: libvirt then creates a fresh
    NVRAM for the new VM from the firmware's template instead of sharing the source's file. The copy boots through
    the UEFI fallback path (\\EFI\\BOOT\\BOOTX64.EFI), which Windows and the usual distributions install."""
    os_el = root.find("os")
    nvram = os_el.find("nvram") if os_el is not None else None
    if nvram is not None:
        os_el.remove(nvram)


def host_support(conn):
    """What this host can offer: {"uefi": bool, "uefi_secure": bool, "raison": str|None}, "raison" explaining the
    first missing piece. Read from libvirt's domain capabilities for q35, so it reflects what libvirt will really
    accept (the OVMF firmware descriptors, a Secure Boot build, swtpm), not a guess from file paths."""
    caps = None
    for virt_type in ("kvm", "qemu"):
        try:
            caps = ET.fromstring(conn.getDomainCapabilities(None, "x86_64", Q35, virt_type, 0))
            break
        except libvirt.libvirtError:
            logger.debug("No %s domain capabilities for q35", virt_type, exc_info=True)
    if caps is None:
        return {"uefi": False, "uefi_secure": False, "raison": "This host's QEMU does not offer the q35 machine"}
    firmwares = {v.text for v in caps.findall("./os/enum[@name='firmware']/value")}
    secure_values = {v.text for v in caps.findall("./os/loader/enum[@name='secure']/value")}
    smm = caps.find("./features/smm")
    tpm = caps.find("./devices/tpm")
    tpm_backends = {v.text for v in caps.findall("./devices/tpm/enum[@name='backendModel']/value")}
    tpm_versions = {v.text for v in caps.findall("./devices/tpm/enum[@name='backendVersion']/value")}

    if "efi" not in firmwares:
        return {
            "uefi": False,
            "uefi_secure": False,
            "raison": "No UEFI firmware on this host: install the ovmf package",
        }
    reason = None
    if "yes" not in secure_values:
        reason = "No Secure Boot build of the UEFI firmware on this host: install a recent ovmf package"
    elif smm is not None and smm.get("supported") != "yes":
        reason = "This host's QEMU does not support SMM, needed by Secure Boot"
    elif tpm is None or tpm.get("supported") != "yes" or "emulator" not in tpm_backends:
        reason = "No software TPM on this host: install the swtpm and swtpm-tools packages"
    elif tpm_versions and "2.0" not in tpm_versions:
        reason = "This host's software TPM does not offer TPM 2.0"
    return {"uefi": True, "uefi_secure": reason is None, "raison": reason}


def check_choice(conn, firmware):
    """None when the host can build a VM with this firmware, else the reason it cannot."""
    if firmware == BIOS:
        return None
    support = host_support(conn)
    if firmware == UEFI and not support["uefi"]:
        return support["raison"]
    if firmware == UEFI_SECURE and not support["uefi_secure"]:
        return support["raison"]
    return None
