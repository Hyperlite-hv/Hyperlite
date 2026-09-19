"""Cluster compatibility diagnostics.

Answers, BEFORE acting, "can this VM migrate to this node?" and "are these two
nodes compatible?", with actionable messages instead of an opaque libvirt
error in the middle of a migration.

Each check returns {id, statut, message, action?} with a status of
ok / warning / blocking (the same shape as app/core/preflight.py, whose
summarize() is reused). A check that cannot run never raises: it becomes a
"check unavailable" `warning`, a controlled degradation that is never a false
block or a global failure.

These are pure functions over already open libvirt connections (local or
qemu+ssh://), so they can be tested with fake connection objects."""

import xml.etree.ElementTree as ET

import libvirt

from app.core.preflight import BLOCKING, OK, WARNING, summarize


def _c(id_, statut, message, action=None):
    d = {"id": id_, "statut": statut, "message": message}
    if action:
        d["action"] = action
    return d


def _ver(v):
    return f"{v // 1000000}.{(v // 1000) % 1000}.{v % 1000}"


def _safe(id_, fn):
    """Run one check; any exception becomes a warning."""
    try:
        return fn()
    except Exception as e:
        return [_c(id_, WARNING, f"Check unavailable ({type(e).__name__}: {str(e)[:120]})")]


def _caps(conn):
    return ET.fromstring(conn.getCapabilities())


def _host_arch(caps):
    return caps.findtext("host/cpu/arch")


def _machine_types(caps, arch):
    """Set of supported machine types (names AND canonical aliases)."""
    out = set()
    for guest in caps.findall("guest"):
        a = guest.find("arch")
        if a is None or a.get("name") != arch:
            continue
        for m in a.findall("machine"):
            if m.text:
                out.add(m.text.strip())
            if m.get("canonical"):
                out.add(m.get("canonical"))
    return out


def _has_kvm(caps):
    return any(d.get("type") == "kvm" for g in caps.findall("guest") for d in g.findall("arch/domain"))


# --- Checks between two hosts -----------------------------------------


def check_pair(src, dst):
    """General source -> destination compatibility, independent of any VM."""
    checks = []

    def arch():
        sa, da = _host_arch(_caps(src)), _host_arch(_caps(dst))
        if sa == da:
            return [_c("architecture", OK, f"Same architecture ({sa})")]
        return [
            _c(
                "architecture",
                BLOCKING,
                f"Different architectures: {sa} -> {da}. A VM cannot migrate across architectures.",
            )
        ]

    def versions():
        out = []
        for label, gs in (("QEMU", lambda c: c.getVersion()), ("libvirt", lambda c: c.getLibVersion())):
            sv, dv = gs(src), gs(dst)
            if dv >= sv:
                out.append(
                    _c(
                        f"version_{label.lower()}",
                        OK,
                        f"{label} {_ver(sv)} -> {_ver(dv)} (destination is the same or newer)",
                    )
                )
            else:
                out.append(
                    _c(
                        f"version_{label.lower()}",
                        WARNING,
                        f"Destination {label} ({_ver(dv)}) is older than the source ({_ver(sv)}): migrating to an earlier version is not guaranteed",
                        action=f"upgrade {label} on the destination",
                    )
                )
        return out

    def kvm():
        sk, dk = _has_kvm(_caps(src)), _has_kvm(_caps(dst))
        if dk:
            return [_c("kvm", OK, "KVM is available on the destination")]
        if sk:
            return [
                _c(
                    "kvm",
                    BLOCKING,
                    "KVM is unavailable on the destination (hardware virtualization missing or disabled)",
                    action="enable VT-x/AMD-V (or nested virtualization) on the destination",
                )
            ]
        return [_c("kvm", WARNING, "KVM is unavailable on both sides (software emulation)")]

    def cpu():
        host_cpu = _caps(src).find("host/cpu")
        if host_cpu is None:
            return [_c("cpu", WARNING, "CPU source non lisible")]
        return [_cpu_result("cpu", dst, ET.tostring(host_cpu, encoding="unicode"), "Physical CPU of the source")]

    for id_, fn in (("architecture", arch), ("versions", versions), ("kvm", kvm), ("cpu", cpu)):
        checks += _safe(id_, fn)
    return checks


def _cpu_result(id_, dst, cpu_xml, what):
    try:
        r = dst.compareCPU(cpu_xml, 0)
    except libvirt.libvirtError as e:
        if "Unknown CPU model" in str(e):
            # A REAL case seen between two machines running different QEMU/libvirt
            # versions: this is neither a proven block nor proven compatibility, because the
            # destination does not know the model.
            return _c(
                id_,
                WARNING,
                f"{what}: model unknown to the destination ({str(e).split('Unknown CPU model')[-1].strip()}); the CPU model databases differ, compatibility cannot be demonstrated",
                action="use a generic CPU (e.g. qemu64 without svm/vmx) for VMs meant to migrate between these nodes",
            )
        raise
    if r == libvirt.VIR_CPU_COMPARE_INCOMPATIBLE:
        return _c(
            id_,
            BLOCKING,
            f"{what} is incompatible with the destination CPU: the VM will not be able to run or migrate there",
            action="use a generic CPU (e.g. qemu64) for this VM, or a newer destination",
        )
    return _c(id_, OK, f"{what} is compatible with the destination")


# --- Checks specific to a VM -------------------------------------


def _netfs_pools(conn):
    """{(host, source directory): local mount path} of the NFS pools."""
    out = {}
    for pool in conn.listAllStoragePools():
        root = ET.fromstring(pool.XMLDesc(0))
        if root.get("type") != "netfs":
            continue
        host_el, dir_el = root.find("source/host"), root.find("source/dir")
        host = host_el.get("name") if host_el is not None else None
        directory = dir_el.get("path") if dir_el is not None else None
        target = root.findtext("target/path")
        if host and directory and target:
            out[(host, directory)] = target
    return out


def check_vm_migration(src, dst, domain):
    """Host-pair checks plus the ones specific to the VM configuration."""
    checks = check_pair(src, dst)
    xml = ET.fromstring(domain.XMLDesc(0))
    name = domain.name()

    def state():
        if domain.isActive():
            return [_c("etat_vm", OK, "VM is running (live migration is possible)")]
        return [
            _c(
                "etat_vm",
                BLOCKING,
                "VM is stopped: live migration requires a running VM",
                action="start the VM, or use disk export/import",
            )
        ]

    def exists():
        try:
            dst.lookupByName(name)
        except libvirt.libvirtError:
            return [_c("nom_libre", OK, f"No VM named '{name}' on the destination")]
        return [_c("nom_libre", BLOCKING, f"A VM named '{name}' already exists on the destination")]

    def machine():
        os_type = xml.find("os/type")
        machine = os_type.get("machine") if os_type is not None else None
        if not machine:
            return [_c("machine", OK, "Machine type is not pinned")]
        arch = os_type.get("arch") or _host_arch(_caps(src))
        supported = _machine_types(_caps(dst), arch)
        if machine in supported:
            return [_c("machine", OK, f"Machine type {machine} is supported by the destination")]
        return [
            _c(
                "machine",
                BLOCKING,
                f"Machine type {machine} is unknown to the destination (QEMU too old)",
                action="upgrade QEMU on the destination, or recreate the VM with a generic machine type ('pc'/'q35' alias)",
            )
        ]

    def cpu_vm():
        cpu = xml.find("cpu")
        if cpu is None:
            return [_c("cpu_vm", OK, "VM CPU is not specific")]
        if cpu.get("mode") == "host-passthrough":
            return [
                _c(
                    "cpu_vm",
                    WARNING,
                    "CPU in host-passthrough mode: migration requires identical physical CPUs",
                    action="prefer host-model or a generic model",
                )
            ]
        return [_cpu_result("cpu_vm", dst, ET.tostring(cpu, encoding="unicode"), "VM CPU")]

    def firmware():
        os_el = xml.find("os")
        loader = os_el.find("loader") if os_el is not None else None
        uses_efi = loader is not None or (os_el is not None and os_el.get("firmware") == "efi")
        if not uses_efi:
            return [_c("firmware", OK, "BIOS boot (no UEFI firmware required)")]
        arch = (os_el.find("type").get("arch") if os_el.find("type") is not None else None) or _host_arch(_caps(src))
        dc = ET.fromstring(dst.getDomainCapabilities(None, arch, None, "kvm", 0))
        if dc.find("os/loader") is None or dc.find("os/loader").get("supported") != "yes":
            return [
                _c(
                    "firmware",
                    BLOCKING,
                    "The destination provides no UEFI firmware (OVMF)",
                    action="apt install ovmf on the destination",
                )
            ]
        return [_c("firmware", OK, "UEFI firmware is available on the destination")]

    def networks():
        out = []
        for iface in xml.findall("devices/interface"):
            src_el = iface.find("source")
            if src_el is None:
                continue
            if iface.get("type") == "network":
                net = src_el.get("network")
                try:
                    n = dst.networkLookupByName(net)
                except libvirt.libvirtError:
                    out.append(
                        _c(
                            f"reseau:{net}",
                            BLOCKING,
                            f"Network '{net}' is missing on the destination",
                            action=f"create a network '{net}' on the destination",
                        )
                    )
                    continue
                out.append(
                    _c(
                        f"reseau:{net}",
                        OK,
                        f"Network '{net}' exists on the destination"
                        + ("" if n.isActive() else " (inactive: started automatically)"),
                    )
                )
            elif iface.get("type") == "bridge":
                out.append(
                    _c(
                        f"pont:{src_el.get('bridge')}",
                        WARNING,
                        f"Bridge '{src_el.get('bridge')}': its existence on the destination cannot be verified remotely",
                        action="check that this bridge exists on the destination",
                    )
                )
        return out or [_c("reseau", OK, "No network interface to check")]

    def disks():
        out = []
        shared = _netfs_pools(src)
        dst_shared = _netfs_pools(dst)
        to_copy_bytes = 0
        for disk in xml.findall("devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target").get("dev")
            if disk.get("type") == "block":
                out.append(
                    _c(
                        f"disque:{target}",
                        BLOCKING,
                        f"Disk {target} is on a block device (ZFS zvol): live migration is not supported by Hyperlite",
                        action="stop the VM and use export/import, or file-based shared storage",
                    )
                )
                continue
            path = disk.find("source").get("file") if disk.find("source") is not None else None
            fmt = disk.find("driver").get("type") if disk.find("driver") is not None else None
            if fmt not in (None, "qcow2", "raw"):
                out.append(_c(f"format:{target}", WARNING, f"Disk format {fmt} ({target}): migration not tested"))
            is_shared = any(path and path.startswith(t) and key in dst_shared for key, t in shared.items())
            if is_shared:
                out.append(_c(f"disque:{target}", OK, f"Disk {target} is on shared storage (no copy)"))
            else:
                try:
                    size = domain.blockInfo(target, 0)[0]
                except libvirt.libvirtError:
                    size = 0
                to_copy_bytes += size
                out.append(
                    _c(f"disque:{target}", OK, f"Disk {target} is copied over the network ({size / 1024**3:.1f} GB)")
                )
        if to_copy_bytes:
            pool = dst.storagePoolLookupByName("default")
            avail = pool.info()[3]
            if avail < to_copy_bytes:
                out.append(
                    _c(
                        "espace_disque",
                        BLOCKING,
                        f"Not enough space on the destination: {avail / 1024**3:.1f} GB free, {to_copy_bytes / 1024**3:.1f} GB to copy",
                        action="free some space or use shared storage",
                    )
                )
            else:
                out.append(_c("espace_disque", OK, f"Enough space on the destination ({avail / 1024**3:.1f} GB free)"))
        return out or [_c("disques", OK, "No disk to check")]

    def memory():
        need_kib = domain.info()[2]
        st = dst.getMemoryStats(libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS, 0)
        avail_kib = st.get("free", 0) + st.get("cached", 0) + st.get("buffers", 0)
        if avail_kib < need_kib:
            return [
                _c(
                    "memoire",
                    BLOCKING,
                    f"Not enough memory on the destination: {avail_kib // 1024} MB available, VM needs {need_kib // 1024} MB",
                )
            ]
        if avail_kib < need_kib * 1.2:
            return [
                _c(
                    "memoire",
                    WARNING,
                    f"Little memory headroom on the destination: {avail_kib // 1024} MB available for {need_kib // 1024} MB",
                )
            ]
        return [_c("memoire", OK, f"Enough memory on the destination ({avail_kib // 1024} MB available)")]

    def hostdev():
        if xml.findall("devices/hostdev"):
            return [
                _c(
                    "peripheriques",
                    BLOCKING,
                    "Passthrough device(s) (hostdev) attached: migration is impossible",
                    action="detach the passthrough devices",
                )
            ]
        return [_c("peripheriques", OK, "No passthrough device")]

    for id_, fn in (
        ("etat_vm", state),
        ("nom_libre", exists),
        ("machine", machine),
        ("cpu_vm", cpu_vm),
        ("firmware", firmware),
        ("reseaux", networks),
        ("disques", disks),
        ("memoire", memory),
        ("peripheriques", hostdev),
    ):
        checks += _safe(id_, fn)
    return checks


def report(checks):
    return {"resume": summarize(checks), "controles": checks}
