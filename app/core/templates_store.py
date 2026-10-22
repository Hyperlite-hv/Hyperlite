import contextlib
import json
import os
import time
import xml.etree.ElementTree as ET

from app.core import firmware, paths
from app.core.safe_paths import safe_child

TEMPLATES_DIR = paths.TEMPLATES
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _meta_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.json")


def _disk_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.qcow2")


def _xml_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.xml")


def disk_path(name):
    return _disk_path(name)


def partial_disk_path(name):
    """Where a template's disk is written while it is copied: a hidden name, never listed nor taken for a template."""
    return safe_child(TEMPLATES_DIR, f".{name}.qcow2.part")


def exists(name):
    return _meta_path(name).exists()


def disk_size_gb(path):
    """A template disk's virtual size in whole GB, rounded up: the smallest disk a VM deployed from it can have (a disk
    grows only). Read from the qcow2 header, a few bytes: the listing stays as quick as before, without running
    qemu-img for every template. None while the disk is being copied."""
    try:
        with open(path, "rb") as f:
            header = f.read(32)
        size = int.from_bytes(header[24:32], "big") if header[:4] == b"QFI\xfb" else os.path.getsize(path)
    except FileNotFoundError:
        return None
    return max(1, -(-size // 1024**3))


def _described(meta):
    """The listing of a template: its stored description, with what a client sizes a deployment by (vSphere's
    template facts): its firmware and its disk, read from its own files, so templates made before are described too."""
    name = meta["name"]
    try:
        root = ET.parse(_xml_path(name)).getroot()
        meta["firmware"] = firmware.of_domain(root)
    except (OSError, ET.ParseError):
        meta["firmware"] = None
    size = disk_size_gb(_disk_path(name))
    meta["disks"] = [] if size is None else [{"size_gb": size}]
    return meta


def list_templates():
    result = []
    for meta_file in sorted(TEMPLATES_DIR.glob("*.json")):
        with open(meta_file) as f:
            result.append(_described(json.load(f)))
    return result


def get_template(name):
    p = _meta_path(name)
    if not p.exists():
        return None
    with open(p) as f:
        meta = json.load(f)
    with open(_xml_path(name)) as f:
        meta["xml"] = f.read()
    meta["disk_path"] = str(_disk_path(name))
    return meta


def save_template(name, xml, vcpu, memory_mb, source_vm, created_by, ssh_user=None, os_label=None):
    """`ssh_user`, `os_label`: the source VM's, which the VMs deployed from the template take (its disk holds the same
    system and the same account)."""
    meta = {
        "name": name,
        "vm_source": source_vm,
        "vcpu": vcpu,
        "memory_mb": memory_mb,
        "created_by": created_by,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if ssh_user:
        meta["ssh_user"] = ssh_user
    if os_label:
        meta["os_label"] = os_label
    with open(_xml_path(name), "w") as f:
        f.write(xml)
    with open(_meta_path(name), "w") as f:
        json.dump(meta, f)
    return _disk_path(name)


def delete_template(name):
    for p in (_meta_path(name), _xml_path(name), _disk_path(name)):
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


def rename_template(old, new):
    """Its three files (description, definition, disk) take the new name. A deployed VM has its own copy of the disk,
    so nothing else refers to the template's files."""
    if not exists(old):
        raise FileNotFoundError(old)
    if exists(new) or _disk_path(new).exists():
        raise FileExistsError(new)
    moved = []
    try:
        for path in (_disk_path, _xml_path, _meta_path):
            if path(old).exists():
                path(old).rename(path(new))
                moved.append(path)
        with open(_meta_path(new)) as f:
            meta = json.load(f)
        meta["name"] = new
        with open(_meta_path(new), "w") as f:
            json.dump(meta, f)
    except OSError:
        for path in moved:
            path(new).rename(path(old))
        raise
