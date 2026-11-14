"""A VM's definition as something to make it again: a body for POST /vms, or the `hyperlite_vm` resource of the
Terraform / OpenTofu provider (docs/design/terraform-provider.md). Read from libvirt's description of the VM; the
disks' contents, snapshots and the guest's own settings are not part of it."""

import json
import re
import xml.etree.ElementTree as ET

import libvirt


def _disk(conn, domain, disk):
    target = disk.find("target")
    source = disk.find("source")
    dev = target.get("dev") if target is not None else None
    try:
        size_gb = -(-domain.blockInfo(dev)[0] // 1024**3) if dev else None
    except libvirt.libvirtError:
        size_gb = None
    pool = None
    if source is not None:
        if source.get("pool"):
            pool = source.get("pool")
        elif source.get("file") or source.get("dev"):
            try:
                pool = (
                    conn.storageVolLookupByPath(source.get("file") or source.get("dev"))
                    .storagePoolLookupByVolume()
                    .name()
                )
            except libvirt.libvirtError:
                pool = None
    return {"size_gb": size_gb, "storage_pool": pool, "target": dev}


def read(conn, domain):
    from app.core import object_meta
    from app.core import permissions as perm

    root = ET.fromstring(domain.XMLDesc(0))
    name = domain.name()
    loader = root.find("./os/loader")
    firmware = "bios"
    if loader is not None or root.find("./os[@firmware='efi']") is not None:
        firmware = "uefi_secure" if (loader is not None and loader.get("secure") == "yes") else "uefi"
    disks = [_disk(conn, domain, d) for d in root.findall("./devices/disk[@device='disk']")]
    interfaces = []
    for nic in root.findall("./devices/interface"):
        source = nic.find("source")
        vlan = nic.find("./vlan/tag")
        interfaces.append(
            {
                "network": source.get("network") or source.get("bridge") if source is not None else None,
                "mac": (nic.find("mac").get("address") if nic.find("mac") is not None else None),
                "vlan_tag": int(vlan.get("id")) if vlan is not None and vlan.get("id", "").isdigit() else None,
            }
        )
    pools = {p["id"]: p["name"] for p in perm.list_pools()}
    member_of = [pools[i] for i in perm.get_vm_pools(name) if i in pools]
    return {
        "name": name,
        "vcpu": domain.info()[3],
        "memory_mb": domain.info()[1] // 1024,
        "firmware": firmware,
        "disks": disks,
        "interfaces": interfaces,
        "resource_pool": member_of[0] if member_of else None,
        "tags": object_meta.get("vm", name)["tags"],
    }


def as_create_body(d):
    """A body for POST /vms: what the creation takes (its first network; the other interfaces are added after)."""
    body = {
        "name": d["name"],
        "vcpu": d["vcpu"],
        "memory_mb": d["memory_mb"],
        "firmware": d["firmware"],
        "disks": [{"size_gb": x["size_gb"] or 1} for x in d["disks"]] or [{"size_gb": 10}],
        "network": (d["interfaces"][0]["network"] if d["interfaces"] else None) or "default",
    }
    pools = {x["storage_pool"] for x in d["disks"] if x["storage_pool"]}
    if len(pools) == 1:
        body["storage_pool"] = pools.pop()
    return body


def _hcl(value):
    return json.dumps(value, ensure_ascii=False)


def as_terraform(d):
    label = re.sub(r"[^A-Za-z0-9_]", "_", d["name"]) or "vm"
    if label[0].isdigit():
        label = f"vm_{label}"
    lines = [f'resource "hyperlite_vm" "{label}" {{', f"  name      = {_hcl(d['name'])}"]
    if d["resource_pool"]:
        lines.append(f"  resource_pool = {_hcl(d['resource_pool'])}")
    lines += [f"  vcpu      = {d['vcpu']}", f"  memory_mb = {d['memory_mb']}", f"  firmware  = {_hcl(d['firmware'])}"]
    for disk in d["disks"]:
        lines += ["", "  disk {", f"    size_gb      = {disk['size_gb'] or 1}"]
        if disk["storage_pool"]:
            lines.append(f"    storage_pool = {_hcl(disk['storage_pool'])}")
        lines.append("  }")
    for nic in d["interfaces"]:
        lines += ["", "  network_interface {", f"    network = {_hcl(nic['network'] or 'default')}"]
        if nic["vlan_tag"] is not None:
            lines.append(f"    vlan_tag = {nic['vlan_tag']}")
        lines.append("  }")
    if d["tags"]:
        lines += ["", f"  tags = {_hcl(d['tags'])}"]
    lines.append("}")
    return "\n".join(lines) + "\n"
