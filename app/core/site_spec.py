"""A VM as it goes to another cluster (docs/design/multi-cluster.md, section 9.1, point 3): in Hyperlite's own terms,
never as its libvirt XML. The source reads what it can carry from its domain (from_domain) and names what it leaves
behind; the target checks every field (check) as it would a VM it creates, then writes a domain of its own from it
(to_domain), with its new volumes and the networks chosen: a remote's XML could name any file of the target host, a
specification names none.

What is carried: the name, UUID, memory, vCPUs and CPU model, the firmware (BIOS, UEFI, UEFI with Secure Boot: the
NVRAM and the TPM are fresh ones, as for a clone or a restore), the machine family, the disks (bus, size, cache,
limits), the cards (MAC address, model, the network chosen), the empty CD-ROM drives, the console's kind, the video,
the guest agent channel, the balloon and the random number generator. What is not: CPU pinning and NUMA placement
(they name the source host's CPUs), a card's firewall filter, a graphics password, the ISO in a CD-ROM. A VM with a
device of this host (PCI or USB passthrough, a shared directory, an iSCSI LUN) is refused."""

import re
import uuid
import xml.etree.ElementTree as ET

from app.core import firmware as fw

MAX_DISKS = 16
MAX_NICS = 16
MAX_CDROMS = 4
_DEV = re.compile(r"^(sd|vd|hd)[a-z]{1,2}$")
_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_CPU_MODEL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CPU_FEATURE = re.compile(r"^[a-z0-9_.-]{1,64}$")
DISK_BUSES = ("virtio", "scsi", "sata", "ide")
NIC_MODELS = ("virtio", "e1000e", "e1000", "rtl8139")
CACHES = ("none", "writeback", "writethrough", "directsync", "unsafe", "default")
DISCARDS = ("unmap", "ignore")
IOTUNE = ("total_bytes_sec", "read_bytes_sec", "write_bytes_sec", "total_iops_sec", "read_iops_sec", "write_iops_sec")
CPU_MODES = ("host-passthrough", "host-model", "custom")
GRAPHICS = ("vnc", "spice")
VIDEOS = ("virtio", "qxl", "vga", "cirrus", "bochs", "none")
MACHINES = ("pc", "q35")
CLOCKS = ("utc", "localtime")


class SpecError(Exception):
    pass


def _int(value, low, high, what):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SpecError(f"{what}: not a number") from None
    if not low <= number <= high:
        raise SpecError(f"{what}: between {low} and {high}")
    return number


def _choice(value, choices, what):
    if value not in choices:
        raise SpecError(f"{what}: one of {', '.join(choices)}")
    return value


# ---- At the source ----


def refusals(root):
    """Why the VM cannot go to another cluster as it is: [sentences], empty when it can."""
    out = []
    devices = root.find("devices")
    devices = devices if devices is not None else ET.Element("devices")
    if devices.find("hostdev") is not None:
        out.append("it uses a device of this host (PCI or USB passthrough)")
    if devices.find("filesystem") is not None:
        out.append("it shares a directory of this host")
    if devices.find("redirdev") is not None or devices.find("smartcard") is not None:
        out.append("it forwards a USB device or a smart card")
    for disk in devices.findall("disk"):
        if disk.get("device") in ("disk", "lun") and disk.get("type") not in ("file", "block"):
            out.append(f"its disk {_target(disk)} is not a file or a block device of this host (iSCSI, network)")
        if disk.get("device") == "lun":
            out.append(f"its disk {_target(disk)} is a LUN passed through")
    disks = [d for d in devices.findall("disk") if d.get("device") == "disk"]
    if not disks:
        out.append("it has no disk")
    if len(disks) > MAX_DISKS or len(devices.findall("interface")) > MAX_NICS:
        out.append(f"it has more than {MAX_DISKS} disks or {MAX_NICS} cards")
    return out


def _target(disk):
    target = disk.find("target")
    return target.get("dev") if target is not None else "?"


def from_domain(xml, disk_sizes):
    """(specification, [what is left behind]) of a stopped VM: `disk_sizes` {target dev: size in bytes, format}."""
    root = ET.fromstring(xml)
    left = []
    os_el = root.find("os")
    os_type = os_el.find("type") if os_el is not None else None
    machine = (os_type.get("machine") if os_type is not None else "") or "pc"
    spec = {
        "name": root.findtext("name"),
        "uuid": root.findtext("uuid"),
        "memory_mb": _kib(root.find("memory")) // 1024,
        "current_memory_mb": _kib(_first(root, "currentMemory", "memory")) // 1024,
        "vcpus": int(root.findtext("vcpu") or 1),
        "firmware": fw.of_domain(root),
        "machine": "q35" if "q35" in machine else "pc",
        "clock": (root.find("clock").get("offset") if root.find("clock") is not None else None) or "utc",
        "description": (root.findtext("description") or "")[:4096],
        "cpu": _cpu(root.find("cpu")),
        "disks": [],
        "cdroms": [],
        "nics": [],
    }
    if root.find("cputune/vcpupin") is not None or root.find("cputune/emulatorpin") is not None:
        left.append("CPU pinning (it names this host's CPUs)")
    if root.find("numatune") is not None:
        left.append("NUMA placement")
    if fw.has_tpm(root):
        left.append("the TPM's content: a fresh TPM there (BitLocker asks for its recovery key)")
    if spec["firmware"] != fw.BIOS:
        left.append("the UEFI variables: a fresh NVRAM there (it boots through the fallback path)")
    devices = root.find("devices")
    for disk in devices.findall("disk"):
        target = disk.find("target")
        dev, bus = target.get("dev"), target.get("bus")
        if disk.get("device") == "cdrom":
            if disk.find("source") is not None:
                left.append(f"the ISO in {dev} (the drive goes empty)")
            spec["cdroms"].append({"dev": dev, "bus": bus})
            continue
        if disk.get("device") != "disk":
            continue
        driver = disk.find("driver")
        size, fmt = disk_sizes[dev]
        entry = {
            "dev": dev,
            "bus": bus,
            "size": int(size),
            "format": fmt,
            "cache": driver.get("cache") if driver is not None else None,
            "discard": driver.get("discard") if driver is not None else None,
            "serial": disk.findtext("serial"),
            "boot": _boot(disk),
            "iotune": {k: int(disk.findtext(f"iotune/{k}")) for k in IOTUNE if disk.findtext(f"iotune/{k}")},
        }
        spec["disks"].append(entry)
    for nic in devices.findall("interface"):
        mac, model = nic.find("mac"), nic.find("model")
        if nic.find("filterref") is not None:
            left.append(f"the firewall filter of {mac.get('address') if mac is not None else 'a card'}")
        source = nic.find("source")
        spec["nics"].append({
            "mac": mac.get("address").lower() if mac is not None else None,
            "model": model.get("type") if model is not None else "virtio",
            "network": (source.get("network") or source.get("bridge")) if source is not None else None,
            "boot": _boot(nic),
        })  # fmt: skip
    graphics = devices.find("graphics")
    if graphics is not None and graphics.get("passwd"):
        left.append("the console's password")
    spec["graphics"] = graphics.get("type") if graphics is not None else None
    video = devices.find("video/model")
    spec["video"] = video.get("type") if video is not None else None
    spec["agent"] = any(
        c.find("target") is not None and c.find("target").get("name") == "org.qemu.guest_agent.0"
        for c in devices.findall("channel")
    )
    balloon = devices.find("memballoon")
    spec["balloon"] = balloon is None or balloon.get("model") != "none"
    spec["rng"] = devices.find("rng") is not None
    return spec, left


def _first(root, *tags):
    for tag in tags:
        element = root.find(tag)
        if element is not None:
            return element
    return None


def _kib(element):
    if element is None:
        return 0
    unit = (element.get("unit") or "KiB").lower()
    factor = {"kib": 1, "k": 1, "mib": 1024, "m": 1024, "gib": 1024**2, "g": 1024**2, "b": 1 / 1024, "bytes": 1 / 1024}
    return int(int(element.text) * factor.get(unit, 1))


def _boot(element):
    boot = element.find("boot")
    return int(boot.get("order")) if boot is not None and boot.get("order") else None


def _cpu(cpu):
    if cpu is None:
        return {"mode": "host-model"}
    out = {"mode": cpu.get("mode") or "custom"}
    model = cpu.find("model")
    if out["mode"] == "custom" and model is not None and model.text:
        out["model"] = model.text.strip()
    topology = cpu.find("topology")
    if topology is not None:
        out["topology"] = {k: int(topology.get(k) or 1) for k in ("sockets", "cores", "threads")}
    features = [{"policy": f.get("policy") or "require", "name": f.get("name")} for f in cpu.findall("feature")]
    if features:
        out["features"] = features
    return out


# ---- At the target ----


def check(spec):
    """The specification with every field checked, as a VM created here: a SpecError on the first one that is not."""
    from app.core.vm_builder import validate_name

    if not isinstance(spec, dict):
        raise SpecError("Not a VM specification")
    problem = validate_name(spec.get("name"))
    if problem:
        raise SpecError(f"name: {problem}")
    try:
        vm_uuid = str(uuid.UUID(str(spec.get("uuid"))))
    except ValueError:
        raise SpecError("uuid: not a UUID") from None
    memory = _int(spec.get("memory_mb"), 64, 16 * 1024 * 1024, "memory_mb")
    out = {
        "name": spec["name"],
        "uuid": vm_uuid,
        "memory_mb": memory,
        "current_memory_mb": _int(spec.get("current_memory_mb", memory), 64, memory, "current_memory_mb"),
        "vcpus": _int(spec.get("vcpus"), 1, 1024, "vcpus"),
        "firmware": _choice(spec.get("firmware"), fw.CHOICES, "firmware"),
        "machine": _choice(spec.get("machine"), MACHINES, "machine"),
        "clock": _choice(spec.get("clock", "utc"), CLOCKS, "clock"),
        "description": str(spec.get("description") or "")[:4096],
        "cpu": _check_cpu(spec.get("cpu") or {}),
        "graphics": None if spec.get("graphics") is None else _choice(spec["graphics"], GRAPHICS, "graphics"),
        "video": None if spec.get("video") is None else _choice(spec["video"], VIDEOS, "video"),
        "agent": bool(spec.get("agent")),
        "balloon": bool(spec.get("balloon", True)),
        "rng": bool(spec.get("rng")),
    }
    if out["firmware"] != fw.BIOS and out["machine"] != "q35":
        raise SpecError("machine: a UEFI VM uses q35")
    disks, cdroms, nics = spec.get("disks"), spec.get("cdroms") or [], spec.get("nics") or []
    if not isinstance(disks, list) or not 1 <= len(disks) <= MAX_DISKS:
        raise SpecError(f"disks: between 1 and {MAX_DISKS}")
    if not isinstance(cdroms, list) or len(cdroms) > MAX_CDROMS or not isinstance(nics, list) or len(nics) > MAX_NICS:
        raise SpecError("Too many CD-ROM drives or cards")
    out["disks"] = [_check_disk(d, i) for i, d in enumerate(disks)]
    out["cdroms"] = [_check_cdrom(c, i) for i, c in enumerate(cdroms)]
    devs = [d["dev"] for d in out["disks"]] + [c["dev"] for c in out["cdroms"]]
    if len(set(devs)) != len(devs):
        raise SpecError("Two drives with the same target")
    out["nics"] = [_check_nic(n, i) for i, n in enumerate(nics)]
    macs = [n["mac"] for n in out["nics"]]
    if len(set(macs)) != len(macs):
        raise SpecError("Two cards with the same MAC address")
    return out


def _check_cpu(cpu):
    if not isinstance(cpu, dict):
        raise SpecError("cpu: not an object")
    out = {"mode": _choice(cpu.get("mode", "host-model"), CPU_MODES, "cpu mode")}
    if out["mode"] == "custom":
        model = cpu.get("model")
        if not isinstance(model, str) or not _CPU_MODEL.match(model):
            raise SpecError("cpu model: a CPU model name")
        out["model"] = model
    if cpu.get("topology") is not None:
        topology = cpu["topology"]
        if not isinstance(topology, dict):
            raise SpecError("cpu topology: not an object")
        out["topology"] = {k: _int(topology.get(k, 1), 1, 256, f"cpu {k}") for k in ("sockets", "cores", "threads")}
    features = cpu.get("features") or []
    if not isinstance(features, list) or len(features) > 512:
        raise SpecError("cpu features: at most 512")
    out["features"] = []
    for f in features:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not _CPU_FEATURE.match(f["name"]):
            raise SpecError("cpu feature: a feature name")
        policy = _choice(f.get("policy", "require"), ("force", "require", "optional", "disable", "forbid"), "policy")
        out["features"].append({"name": f["name"], "policy": policy})
    return out


def _check_dev(value, what):
    if not isinstance(value, str) or not _DEV.match(value):
        raise SpecError(f"{what}: a drive name such as sda or vdb")
    return value


def _check_disk(disk, i):
    what = f"disk {i + 1}"
    if not isinstance(disk, dict):
        raise SpecError(f"{what}: not an object")
    iotune = disk.get("iotune") or {}
    if not isinstance(iotune, dict) or set(iotune) - set(IOTUNE):
        raise SpecError(f"{what}: unknown limits")
    serial = disk.get("serial")
    if serial is not None and (not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,36}", serial)):
        raise SpecError(f"{what}: a serial of letters and digits")
    return {
        "dev": _check_dev(disk.get("dev"), what),
        "bus": _choice(disk.get("bus"), DISK_BUSES, f"{what} bus"),
        "size": _int(disk.get("size"), 1024 * 1024, 64 * 1024**4, f"{what} size"),
        "format": _choice(disk.get("format"), ("qcow2", "raw"), f"{what} format"),
        "cache": None if disk.get("cache") is None else _choice(disk["cache"], CACHES, f"{what} cache"),
        "discard": None if disk.get("discard") is None else _choice(disk["discard"], DISCARDS, f"{what} discard"),
        "serial": serial,
        "boot": None if disk.get("boot") is None else _int(disk["boot"], 1, 64, f"{what} boot order"),
        "iotune": {k: _int(v, 0, 2**53, f"{what} {k}") for k, v in iotune.items()},
    }


def _check_cdrom(cdrom, i):
    if not isinstance(cdrom, dict):
        raise SpecError(f"CD-ROM {i + 1}: not an object")
    return {
        "dev": _check_dev(cdrom.get("dev"), f"CD-ROM {i + 1}"),
        "bus": _choice(cdrom.get("bus"), ("ide", "sata", "scsi"), f"CD-ROM {i + 1} bus"),
    }


def _check_nic(nic, i):
    what = f"card {i + 1}"
    if not isinstance(nic, dict):
        raise SpecError(f"{what}: not an object")
    mac = str(nic.get("mac") or "").lower()
    if not _MAC.match(mac) or int(mac[:2], 16) & 1:
        raise SpecError(f"{what}: a unicast MAC address")
    return {
        "mac": mac,
        "model": _choice(nic.get("model", "virtio"), NIC_MODELS, f"{what} model"),
        "boot": None if nic.get("boot") is None else _int(nic["boot"], 1, 64, f"{what} boot order"),
    }


def to_domain(spec, volumes, networks):
    """The domain XML of a checked specification, written here: `volumes` [(path, "file" or "block")] for its disks
    in order (the target's new volumes), `networks` [name of a network of this host] for its cards in order."""
    if len(volumes) != len(spec["disks"]) or len(networks) != len(spec["nics"]):
        raise SpecError("A volume for every disk and a network for every card")
    root = ET.Element("domain", type="kvm")
    ET.SubElement(root, "name").text = spec["name"]
    ET.SubElement(root, "uuid").text = spec["uuid"]
    if spec["description"]:
        ET.SubElement(root, "description").text = spec["description"]
    ET.SubElement(root, "memory", unit="MiB").text = str(spec["memory_mb"])
    ET.SubElement(root, "currentMemory", unit="MiB").text = str(spec["current_memory_mb"])
    ET.SubElement(root, "vcpu", placement="static").text = str(spec["vcpus"])
    for element in ET.fromstring(f"<x>{fw.os_xml(spec['firmware'])}{fw.features_xml(spec['firmware'])}</x>"):
        root.append(element)
    if spec["firmware"] == fw.BIOS and spec["machine"] == "q35":
        root.find("os/type").set("machine", "q35")
    _write_cpu(root, spec["cpu"])
    ET.SubElement(root, "clock", offset=spec["clock"])
    for tag, value in (("on_poweroff", "destroy"), ("on_reboot", "restart"), ("on_crash", "destroy")):
        ET.SubElement(root, tag).text = value
    devices = ET.SubElement(root, "devices")
    if any(d["bus"] == "scsi" for d in spec["disks"]) or any(c["bus"] == "scsi" for c in spec["cdroms"]):
        ET.SubElement(devices, "controller", type="scsi", index="0", model="virtio-scsi")
    for disk, (path, kind) in zip(spec["disks"], volumes, strict=True):
        element = ET.SubElement(devices, "disk", type=kind, device="disk")
        driver = ET.SubElement(element, "driver", name="qemu", type="raw" if kind == "block" else disk["format"])
        for attr in ("cache", "discard"):
            if disk[attr]:
                driver.set(attr, disk[attr])
        ET.SubElement(element, "source", **({"dev": path} if kind == "block" else {"file": path}))
        ET.SubElement(element, "target", dev=disk["dev"], bus=disk["bus"])
        if disk["serial"]:
            ET.SubElement(element, "serial").text = disk["serial"]
        if disk["iotune"]:
            iotune = ET.SubElement(element, "iotune")
            for key, value in disk["iotune"].items():
                ET.SubElement(iotune, key).text = str(value)
        if disk["boot"]:
            ET.SubElement(element, "boot", order=str(disk["boot"]))
    for cdrom in spec["cdroms"]:
        element = ET.SubElement(devices, "disk", type="file", device="cdrom")
        ET.SubElement(element, "driver", name="qemu", type="raw")
        ET.SubElement(element, "target", dev=cdrom["dev"], bus=cdrom["bus"])
        ET.SubElement(element, "readonly")
    for nic, network in zip(spec["nics"], networks, strict=True):
        element = ET.SubElement(devices, "interface", type="network")
        ET.SubElement(element, "mac", address=nic["mac"])
        ET.SubElement(element, "source", network=network)
        ET.SubElement(element, "model", type=nic["model"])
        if nic["boot"]:
            ET.SubElement(element, "boot", order=str(nic["boot"]))
    serial = ET.SubElement(devices, "serial", type="pty")
    ET.SubElement(serial, "target", port="0")
    console = ET.SubElement(devices, "console", type="pty")
    ET.SubElement(console, "target", type="serial", port="0")
    if spec["agent"]:
        channel = ET.SubElement(devices, "channel", type="unix")
        ET.SubElement(channel, "target", type="virtio", name="org.qemu.guest_agent.0")
    if spec["graphics"]:
        graphics = ET.SubElement(devices, "graphics", type=spec["graphics"], autoport="yes")
        ET.SubElement(graphics, "listen", type="address", address="127.0.0.1")
    if spec["video"]:
        video = ET.SubElement(devices, "video")
        ET.SubElement(video, "model", type=spec["video"])
    ET.SubElement(devices, "input", type="tablet", bus="usb")
    ET.SubElement(devices, "memballoon", model="virtio" if spec["balloon"] else "none")
    if spec["rng"]:
        rng = ET.SubElement(devices, "rng", model="virtio")
        ET.SubElement(rng, "backend", model="random").text = "/dev/urandom"
    tpm = fw.tpm_xml(spec["firmware"])
    if tpm:
        devices.append(ET.fromstring(tpm.strip()))
    return ET.tostring(root, encoding="unicode")


def _write_cpu(root, cpu):
    element = ET.SubElement(root, "cpu", mode=cpu["mode"])
    if cpu["mode"] == "custom":
        ET.SubElement(element, "model", fallback="allow").text = cpu["model"]
    if cpu.get("topology"):
        ET.SubElement(element, "topology", **{k: str(v) for k, v in cpu["topology"].items()})
    for feature in cpu.get("features") or []:
        ET.SubElement(element, "feature", policy=feature["policy"], name=feature["name"])
