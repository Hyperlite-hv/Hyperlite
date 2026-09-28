"""CPU pinning and NUMA placement of a VM (vSphere "Scheduling Affinity", Proxmox `affinity`).

Three choices, applied to the VM definition and, when it runs, to the live VM:
  - no pinning (the default): the host scheduler places the vCPUs anywhere;
  - a set of host CPUs ("4-7"): every vCPU and QEMU's own emulator threads run only on those CPUs, and move freely
    among them. This isolates a noisy VM, or keeps a VM on the cores the others do not use;
  - strict 1:1 ("strict"): vCPU 0 on the first CPU of the set, vCPU 1 on the second, and so on. For a VM that needs
    stable latency (a database, audio, a game). The set must hold at least as many CPUs as the VM has vCPUs.

NUMA: on a host with several NUMA cells (two processor sockets, usually), a VM whose CPUs all sit in one cell also
gets its memory from that cell (<numatune> strict), so it never reads RAM across the socket link. Nothing is done on a
single-cell host, where memory placement makes no difference.

The XML written:
  <vcpu cpuset='4-7'>N</vcpu>           the default for every vCPU, including ones added later
  <cputune>
    <vcpupin vcpu='0' cpuset='4'/>      strict mode only
    <emulatorpin cpuset='4-7'/>
  </cputune>
  <numatune><memory mode='strict' nodeset='1'/></numatune>   multi-cell host, one cell only

Pinning names host CPUs: a VM migrated to a host without those CPUs cannot start there. Live migration is refused
by libvirt in that case with its own error; the docs say to unpin first.
"""

import logging
import re
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)

CPUSET_RE = re.compile(r"^\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*$")
# Beyond this, a cpuset is a typo, not a host: libvirt's own limit on x86 hosts is far lower in practice.
MAX_CPU_ID = 4095


class PinningError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_cpuset(text):
    """ "0-3,6" -> [0, 1, 2, 3, 6]. Only digits, commas and ascending ranges: the value ends up in the domain XML."""
    text = (text or "").replace(" ", "")
    if not CPUSET_RE.match(text):
        raise PinningError(f"Invalid CPU list '{text}': expected for example 2-5 or 0,2,4")
    cpus = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        lo, hi = int(first), int(last or first)
        if lo > hi:
            raise PinningError(f"Invalid CPU range '{part}': the first CPU must come first")
        if hi > MAX_CPU_ID:
            raise PinningError(f"CPU {hi} does not exist")
        cpus.update(range(lo, hi + 1))
    return sorted(cpus)


def format_cpuset(cpus):
    """[0, 1, 2, 3, 6] -> "0-3,6"."""
    parts, cpus = [], sorted(set(cpus))
    i = 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        parts.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(parts)


def host_topology(conn):
    """{"cpus": [{"id", "socket", "coeur", "cellule", "freres"}], "cellules": [{"id", "cpus", "memoire_mo"}]} from
    libvirt's host capabilities. "freres" lists the hyperthreads sharing the physical core."""
    root = ET.fromstring(conn.getCapabilities())
    cpus, cells = [], []
    for cell in root.findall("./host/topology/cells/cell"):
        cell_id = int(cell.get("id"))
        ids = []
        for cpu in cell.findall("./cpus/cpu"):
            # An offline CPU is listed without its socket_id: it cannot run anything.
            if cpu.get("socket_id") is None:
                continue
            cpu_id = int(cpu.get("id"))
            ids.append(cpu_id)
            siblings = cpu.get("siblings") or str(cpu_id)
            cpus.append(
                {
                    "id": cpu_id,
                    "socket": int(cpu.get("socket_id")),
                    "coeur": int(cpu.get("core_id") or 0),
                    "cellule": cell_id,
                    "freres": parse_cpuset(siblings),
                }
            )
        memory = cell.find("memory")
        memory_mb = round(int(memory.text) / 1024) if memory is not None and memory.text else None
        cells.append({"id": cell_id, "cpus": format_cpuset(ids), "memoire_mo": memory_mb})
    return {"cpus": sorted(cpus, key=lambda c: c["id"]), "cellules": cells}


def read(domain):
    """The pinning in the VM's persistent definition: {"cpuset": "4-7"|None, "strict": bool, "numa_cellule": id|None}."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    vcpu_el = root.find("vcpu")
    nvcpu = int((vcpu_el.text or "1").strip()) if vcpu_el is not None else 1
    cpuset = vcpu_el.get("cpuset") if vcpu_el is not None else None
    pins = {int(p.get("vcpu")): p.get("cpuset") for p in root.findall("./cputune/vcpupin")}
    emulator = root.find("./cputune/emulatorpin")
    if not cpuset and emulator is not None:
        cpuset = emulator.get("cpuset")
    if not cpuset and pins:
        cpuset = format_cpuset({c for value in pins.values() for c in parse_cpuset(value)})
    strict = bool(pins) and all(vcpu in pins and len(parse_cpuset(pins[vcpu])) == 1 for vcpu in range(nvcpu))
    memory = root.find("./numatune/memory")
    numa_cell = None
    if memory is not None and memory.get("mode") == "strict" and (memory.get("nodeset") or "").isdigit():
        numa_cell = int(memory.get("nodeset"))
    return {
        "cpuset": format_cpuset(parse_cpuset(cpuset)) if cpuset else None,
        "strict": strict,
        "numa_cellule": numa_cell,
    }


def plan(topology, nvcpu, cpuset_text, strict):
    """Validate a request against the host. Returns {"cpus": [...], "strict", "numa_cellule"} or raises PinningError.
    cpuset_text None means no pinning."""
    if not cpuset_text:
        if strict:
            raise PinningError("Strict pinning needs a list of host CPUs")
        return {"cpus": None, "strict": False, "numa_cellule": None}
    cpus = parse_cpuset(cpuset_text)
    known = {c["id"]: c for c in topology["cpus"]}
    missing = [c for c in cpus if c not in known]
    if missing:
        raise PinningError(
            f"This host has no CPU {format_cpuset(missing)} (its CPUs: {format_cpuset(known) or 'none reported'})"
        )
    if strict and len(cpus) < nvcpu:
        raise PinningError(
            f"Strict pinning puts each vCPU on its own host CPU: this VM has {nvcpu} vCPUs but only {len(cpus)} CPUs are listed"
        )
    cells = {known[c]["cellule"] for c in cpus}
    numa_cell = cells.pop() if len(topology["cellules"]) > 1 and len(cells) == 1 else None
    return {"cpus": cpus, "strict": strict, "numa_cellule": numa_cell}


def apply_to_xml(root, nvcpu, how):
    """Write the plan into a parsed persistent domain XML (see the module docstring for the elements)."""
    vcpu_el = root.find("vcpu")
    cputune = root.find("cputune")
    if cputune is not None:
        for tag in ("vcpupin", "emulatorpin"):
            for el in cputune.findall(tag):
                cputune.remove(el)
    numatune = root.find("numatune")
    if numatune is not None:
        root.remove(numatune)
    if vcpu_el is not None and "cpuset" in vcpu_el.attrib:
        del vcpu_el.attrib["cpuset"]

    if how["cpus"]:
        cpuset = format_cpuset(how["cpus"])
        if vcpu_el is not None:
            vcpu_el.set("cpuset", cpuset)
        if cputune is None:
            cputune = ET.Element("cputune")
            # After <vcpu>/<iothreads>, where libvirt writes it; libvirt reorders elements anyway on define.
            index = list(root).index(vcpu_el) + 1 if vcpu_el is not None else len(root)
            root.insert(index, cputune)
        if how["strict"]:
            for vcpu in range(nvcpu):
                ET.SubElement(cputune, "vcpupin", {"vcpu": str(vcpu), "cpuset": str(how["cpus"][vcpu])})
        ET.SubElement(cputune, "emulatorpin", {"cpuset": cpuset})
        if how["numa_cellule"] is not None:
            numatune = ET.Element("numatune")
            ET.SubElement(numatune, "memory", {"mode": "strict", "nodeset": str(how["numa_cellule"])})
            root.insert(list(root).index(cputune) + 1, numatune)
    if cputune is not None and len(cputune) == 0 and not cputune.attrib:
        root.remove(cputune)


def _cpumap(cpus, host_cpu_count):
    wanted = set(cpus)
    return tuple(i in wanted for i in range(host_cpu_count))


def apply_live(domain, conn, nvcpu, how):
    """Pin a running VM's threads now (the memory placement takes effect at the next start: moving the RAM of a
    running guest between cells is not something libvirt does for a strict policy)."""
    total = conn.getCPUMap(0)[0]
    everything = tuple(True for _ in range(total))
    affinity = _cpumap(how["cpus"], total) if how["cpus"] else everything
    for vcpu in range(nvcpu):
        cpumap = _cpumap([how["cpus"][vcpu]], total) if how["strict"] else affinity
        domain.pinVcpuFlags(vcpu, cpumap, libvirt.VIR_DOMAIN_AFFECT_LIVE)
    domain.pinEmulator(affinity, libvirt.VIR_DOMAIN_AFFECT_LIVE)
