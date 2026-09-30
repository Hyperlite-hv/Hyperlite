import contextlib
import json
import time
from pathlib import Path

from app.core.safe_paths import safe_child

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _meta_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.json")


def _disk_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.qcow2")


def _xml_path(name):
    return safe_child(TEMPLATES_DIR, f"{name}.xml")


def exists(name):
    return _meta_path(name).exists()


def list_templates():
    result = []
    for meta_file in sorted(TEMPLATES_DIR.glob("*.json")):
        with open(meta_file) as f:
            result.append(json.load(f))
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


def save_template(name, xml, vcpu, memory_mb, source_vm, created_by):
    meta = {
        "nom": name,
        "vm_source": source_vm,
        "vcpu": vcpu,
        "memoire_mo": memory_mb,
        "cree_par": created_by,
        "cree_le": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
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
        meta["nom"] = new
        with open(_meta_path(new), "w") as f:
            json.dump(meta, f)
    except OSError:
        for path in moved:
            path(new).rename(path(old))
        raise
