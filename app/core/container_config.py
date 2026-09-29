"""Settings of an existing container: its resources, its network interfaces, its DNS servers, start at boot.

Containers share the host's kernel: memory is a cgroup limit that libvirt changes live, while the number of CPUs
is read by the container when it starts (a change applies at the next start). DNS servers live in the
container's own /etc/resolv.conf, written as a plain file (never through a link that could lead out of it).
"""

import ipaddress
import xml.etree.ElementTree as ET

import libvirt

from app.core.container_builder import container_rootfs_path

MAX_DNS = 3


class ConfigError(ValueError):
    pass


def interfaces(domain):
    """[{"reseau", "mac"}] of the container's network interfaces."""
    root = ET.fromstring(domain.XMLDesc(0))
    out = []
    for iface in root.findall("./devices/interface"):
        source = iface.find("source")
        mac = iface.find("mac")
        out.append(
            {
                "reseau": (source.get("network") or source.get("bridge")) if source is not None else None,
                "mac": mac.get("address") if mac is not None else None,
            }
        )
    return out


def _resolv_conf(name):
    return container_rootfs_path(name) / "etc" / "resolv.conf"


def read_dns(name):
    """The nameservers of the container, or None when its resolver is not a plain file Hyperlite can read."""
    path = _resolv_conf(name)
    if path.is_symlink() or not path.is_file():
        return None
    servers = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return servers


def validate_dns(servers):
    cleaned = []
    for raw in servers:
        value = str(raw).strip()
        if not value:
            continue
        try:
            cleaned.append(str(ipaddress.ip_address(value)))
        except ValueError:
            raise ConfigError(f"'{value}' is not an IP address") from None
    if not cleaned:
        raise ConfigError("At least one DNS server is needed")
    if len(cleaned) > MAX_DNS:
        raise ConfigError(f"At most {MAX_DNS} DNS servers (the resolver ignores the others)")
    return cleaned


def write_dns(name, servers):
    servers = validate_dns(servers)
    path = _resolv_conf(name)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ConfigError("This container has no /etc directory Hyperlite can write to")
    if path.is_symlink():
        path.unlink()  # a link into the image could point anywhere once resolved on the host
    tmp = path.with_name("resolv.conf.hyperlite-tmp")
    tmp.write_text("".join(f"nameserver {s}\n" for s in servers))
    tmp.chmod(0o644)
    tmp.replace(path)
    return servers


def set_resources(conn, domain, vcpu=None, memory_mb=None):
    """Change the definition, and the memory live when the container runs. Returns {"a_redemarrer": bool}: True
    when part of the change only applies at the next start."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    restart = False
    if memory_mb is not None:
        kib = int(memory_mb) * 1024
        for tag in ("memory", "currentMemory"):
            el = root.find(tag)
            if el is None:
                el = ET.SubElement(root, tag)
            el.text = str(kib)
            el.set("unit", "KiB")
    if vcpu is not None:
        el = root.find("vcpu")
        if el is None:
            el = ET.SubElement(root, "vcpu")
        if el.text != str(int(vcpu)):
            el.text = str(int(vcpu))
            restart = restart or bool(domain.isActive())
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    if memory_mb is not None and domain.isActive():
        try:
            domain.setMemoryFlags(int(memory_mb) * 1024, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        except libvirt.libvirtError:
            # Above the limit the running container was started with: it applies at the next start.
            restart = True
    return {"a_redemarrer": restart}
