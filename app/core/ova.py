"""VMs exported from VMware as an OVA (an OVF descriptor, its disks and a manifest in one tar file), as ESXi, vCenter,
Workstation and ovftool write them (docs/design/ova-import.md).

read() maps the descriptor to what a Hyperlite VM is made of: name, vCPUs, memory, disks with their size, firmware
(BIOS, UEFI, UEFI with Secure Boot), the guest's family (which sets the disk and network models, as at creation) and the
network cards. extract_disks() copies the disks out of the tar into the imported disks (app/routers/vm_disks.py),
checking each against the manifest when there is one; the VM is then created from them as from any imported disk.

The file comes from a user: only plain members with plain names are read, never extracted by tarfile itself; the
descriptor is refused with a DTD (an OVF never has one, and entity expansion is how XML files blow up); the disk names
come from the descriptor and must name members of the archive.
"""

import hashlib
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "ovf": "http://schemas.dmtf.org/ovf/envelope/1",
    "rasd": "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData",
    "vmw": "http://www.vmware.com/schema/ovf",
}
OVF = "{" + NS["ovf"] + "}"
VMW = "{" + NS["vmw"] + "}"
MAX_OVF_BYTES = 4 * 2**20
# The disk formats qemu-img reads, as the imported disks accept them.
DISK_SUFFIXES = (".vmdk", ".qcow2", ".img", ".raw", ".vhd", ".vhdx", ".vdi")
MEMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,200}$")
COPY_CHUNK = 4 * 2**20
# CIM resource types (DSP1041) used in an OVF's virtual hardware.
CPU, MEMORY, NIC, DISK = "3", "4", "10", "17"


class OvaError(ValueError):
    """An OVA refused, with a message for the user."""


def _members(tar):
    """{name: member} of the plain files with plain names; a link, a device or a path is refused."""
    found = {}
    for m in tar.getmembers():
        if not m.isfile() or not MEMBER_RE.fullmatch(m.name):
            raise OvaError(f"The OVA holds an entry Hyperlite does not read: {m.name!r}")
        found[m.name] = m
    return found


def _open(path):
    try:
        return tarfile.open(path, mode="r:")
    except (tarfile.TarError, OSError) as e:
        raise OvaError(f"Not an OVA (a tar file): {e}") from None


def _descriptor(tar, members):
    names = [n for n in members if n.lower().endswith(".ovf")]
    if len(names) != 1:
        raise OvaError("An OVA holds exactly one .ovf descriptor")
    member = members[names[0]]
    if member.size > MAX_OVF_BYTES:
        raise OvaError("The OVF descriptor is too large")
    data = tar.extractfile(member).read()
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise OvaError("The OVF descriptor declares a DTD, which an OVF never does")
    try:
        return names[0], ET.fromstring(data)
    except ET.ParseError as e:
        raise OvaError(f"The OVF descriptor is not valid XML: {e}") from None


def _attr(el, name):
    """An attribute in the OVF namespace, or without one (both are seen in the wild)."""
    return el.get(OVF + name, el.get(name))


def _bytes(quantity, units):
    """A quantity in OVF allocation units ("byte", "byte * 2^20", "MegaBytes", "GB"…) as bytes."""
    value = int(quantity)
    units = (units or "byte").replace(" ", "").lower()
    m = re.fullmatch(r"byte\*2\^(\d+)", units)
    if m:
        return value * 2 ** int(m.group(1))
    factor = {"byte": 1, "bytes": 1, "kilobytes": 2**10, "kb": 2**10, "megabytes": 2**20, "mb": 2**20}
    factor |= {"gigabytes": 2**30, "gb": 2**30}
    if units not in factor:
        raise OvaError(f"Unknown allocation units in the OVF: {units}")
    return value * factor[units]


def _family(os_type, description):
    """The guest's family, as the creation form names it: a Windows guest gets SATA disks and an e1000e card."""
    text = f"{os_type or ''} {description or ''}".lower()
    if "windows" in text or text.startswith("win"):
        return "windows"
    if any(w in text for w in ("linux", "ubuntu", "debian", "centos", "rhel", "sles", "fedora", "alma", "rocky")):
        return "linux"
    return "other"


def _configs(section):
    """vmw:Config key/value pairs of the virtual hardware (vmw:key in vSphere 6.5 and later, ovf:key before)."""
    found = {}
    for c in section.findall("vmw:Config", NS):
        key = c.get(VMW + "key", c.get(OVF + "key"))
        if key:
            found[key] = c.get(VMW + "value", c.get(OVF + "value"))
    return found


def read(path):
    """What the OVA's VM is made of, mapped to Hyperlite: name, vcpu, memory_mb, firmware, guest_os, os, disks
    [{file, capacity_gb, size}], nics [{network, model}]."""
    try:
        return _read(path)
    except OvaError:
        raise
    except (ValueError, TypeError, AttributeError, tarfile.TarError, EOFError) as e:
        # A number that is not one, an element missing where the OVF schema requires it, a truncated archive.
        raise OvaError(f"The OVA could not be read: {e}") from None


def _read(path):
    with _open(path) as tar:
        members = _members(tar)
        _ovf_name, root = _descriptor(tar, members)
    files = {_attr(f, "id"): f for f in root.findall("ovf:References/ovf:File", NS)}
    disks_by_id = {_attr(d, "diskId"): d for d in root.findall("ovf:DiskSection/ovf:Disk", NS)}
    systems = root.findall("ovf:VirtualSystem", NS)
    if len(systems) != 1:
        raise OvaError("The OVA holds several VMs (a vApp): export them one by one")
    system = systems[0]
    hardware = system.find("ovf:VirtualHardwareSection", NS)
    if hardware is None:
        raise OvaError("The OVF has no virtual hardware section")
    vcpu = memory = None
    disks, nics = [], []
    for item in hardware.findall("ovf:Item", NS):
        kind = item.findtext("rasd:ResourceType", default="", namespaces=NS).strip()
        if kind == CPU:
            vcpu = int(item.findtext("rasd:VirtualQuantity", namespaces=NS))
        elif kind == MEMORY:
            memory = _bytes(
                item.findtext("rasd:VirtualQuantity", namespaces=NS),
                item.findtext("rasd:AllocationUnits", namespaces=NS),
            )
        elif kind == NIC:
            nics.append(
                {
                    "network": item.findtext("rasd:Connection", namespaces=NS),
                    "model": item.findtext("rasd:ResourceSubType", namespaces=NS),
                }
            )
        elif kind == DISK:
            resource = item.findtext("rasd:HostResource", default="", namespaces=NS)
            disk = disks_by_id.get(resource.rsplit("/", 1)[-1])
            if disk is None:
                raise OvaError(f"A disk of the OVF names a missing disk: {resource}")
            file = files.get(_attr(disk, "fileRef"))
            if file is None:
                raise OvaError(f"Disk {_attr(disk, 'diskId')} of the OVF has no file (an empty disk is not imported)")
            if _attr(file, "compression") or _attr(file, "chunkSize"):
                raise OvaError("Compressed or chunked disk files are not read: export the OVA without compression")
            href = _attr(file, "href")
            if href not in members or not href.lower().endswith(DISK_SUFFIXES):
                raise OvaError(f"Disk file {href!r} is not in the OVA, or not a disk format Hyperlite reads")
            capacity = _bytes(_attr(disk, "capacity"), _attr(disk, "capacityAllocationUnits"))
            disks.append({"file": href, "capacity_gb": max(1, -(-capacity // 2**30)), "size": members[href].size})
    if not vcpu or not memory or not disks:
        raise OvaError("The OVF names no vCPU, no memory or no disk")
    configs = _configs(hardware)
    firmware = "bios"
    if (configs.get("firmware") or "").lower() == "efi":
        firmware = "uefi_secure" if (configs.get("uefi.secureBoot.enabled") or "").lower() == "true" else "uefi"
    os_section = system.find("ovf:OperatingSystemSection", NS)
    os_type = os_section.get(VMW + "osType") if os_section is not None else None
    os_description = os_section.findtext("ovf:Description", namespaces=NS) if os_section is not None else None
    return {
        "name": system.findtext("ovf:Name", namespaces=NS) or _attr(system, "id"),
        "vcpu": vcpu,
        "memory_mb": max(1, memory // 2**20),
        "firmware": firmware,
        "guest_os": _family(os_type, os_description),
        "os": os_description or os_type,
        "disks": disks,
        "nics": nics,
    }


def _manifest(tar, members):
    """{file: (algorithm, digest)} from the OVA's .mf, or {} when it has none."""
    names = [n for n in members if n.lower().endswith(".mf")]
    if not names:
        return {}
    found = {}
    for line in tar.extractfile(members[names[0]]).read().decode("utf-8", "replace").splitlines():
        m = re.fullmatch(r"\s*(SHA1|SHA256|SHA512)\((.+)\)\s*=\s*([0-9a-fA-F]+)\s*", line)
        if m:
            found[m.group(2)] = (m.group(1).lower(), m.group(3).lower())
    return found


def extract_disks(path, dest_dir, stem):
    """Copy the OVA's disks into dest_dir as `<stem>-disk<N><suffix>`, in the VM's disk order, each checked against
    the manifest when there is one. Returns the new file names. A failure removes what was copied."""
    info = read(path)
    dest_dir = Path(dest_dir)
    needed = sum(d["size"] for d in info["disks"])
    if shutil.disk_usage(dest_dir).free < needed + 2**30:
        raise OvaError(f"Not enough room to extract the disks: {needed // 2**20} MiB needed")
    written = []
    try:
        with _open(path) as tar:
            members = _members(tar)
            manifest = _manifest(tar, members)
            for n, disk in enumerate(info["disks"], start=1):
                target = dest_dir / f"{stem}-disk{n}{Path(disk['file']).suffix.lower()}"
                if target.exists():
                    raise OvaError(f"An imported disk {target.name} exists already")
                algorithm, expected = manifest.get(disk["file"], (None, None))
                digest = hashlib.new(algorithm) if algorithm else None
                source = tar.extractfile(members[disk["file"]])
                written.append(target)
                with open(target, "xb") as out:
                    try:
                        while chunk := source.read(COPY_CHUNK):
                            out.write(chunk)
                            if digest:
                                digest.update(chunk)
                    except (tarfile.TarError, EOFError) as e:
                        raise OvaError(f"{disk['file']} is cut short in the OVA: the file is damaged ({e})") from None
                if target.stat().st_size != disk["size"]:
                    raise OvaError(f"{disk['file']} is cut short in the OVA: the file is damaged")
                if digest and digest.hexdigest() != expected:
                    raise OvaError(f"{disk['file']} does not match the OVA's manifest: the file is damaged")
    except BaseException:
        for target in written:
            target.unlink(missing_ok=True)
        raise
    return [t.name for t in written]
