"""LVM storage pools (docs/design/lvm-storage.md): libvirt's `logical` pool, and what it does not do, read from LVM
itself.

Checked on a real volume group (libvirt 10.0, LVM 2.03): libvirt builds a volume group on an empty disk and makes
thick volumes in it, but it cannot resize one ("storage pool does not support changing of volume capacity"), and it
neither lists nor deletes thin volumes or their thin pool. Its pool deletion (`virStoragePoolDelete`) removes the
whole volume group, thin volumes included: Hyperlite never calls it, and removes a pool only while `lvs` shows no
volume in it at all.
"""

import json
import re
import shlex
import subprocess

TIMEOUT_S = 60
# LVM's own rule for volume group and volume names, without the characters a shell or a path would read.
LVM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,126}$")


class LvmError(Exception):
    pass


def _run(args, node=None):
    """An LVM command on this host, or on a registered node over the cluster's SSH connection."""
    if node:
        from app.core import node_health
        from app.core.cluster import get_node, node_ssh_options

        target = get_node(node)
        if target is None:
            raise LvmError(f"Node '{node}' not found")
        args = [
            "ssh",
            *node_ssh_options(["-o", f"ConnectTimeout={node_health.CONNECT_TIMEOUT_S}"]),
            "-p",
            str(target["ssh_port"]),
            f"{target['ssh_user']}@{target['hostname']}",
            shlex.join(args),
        ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LvmError(f"{args[0]}: {e}") from None
    if r.returncode != 0:
        raise LvmError((r.stderr or r.stdout).strip()[-500:] or f"exit status {r.returncode}")
    return r.stdout


def check_name(name, what="volume group"):
    if not isinstance(name, str) or not LVM_NAME_RE.fullmatch(name) or name in (".", ".."):
        raise LvmError(f"Invalid {what} name: letters, digits and . _ + -, starting with a letter or a digit")
    return name


def _report(command, fields, *args, node=None):
    out = _run(["lvm", command, "--reportformat", "json", "--units", "b", "--nosuffix", "-o", fields, *args], node)
    report = json.loads(out)["report"]
    return report[0].get(command[:-1] if command != "lvs" else "lv", []) if report else []


def volume_groups(node=None):
    """The node's volume groups: name, size and free bytes."""
    rows = _report("vgs", "vg_name,vg_size,vg_free", node=node)
    return [{"name": r["vg_name"], "size": int(r["vg_size"]), "free": int(r["vg_free"])} for r in rows]


def _kind(attr):
    """lv_attr's first letter: what the volume is."""
    return {"t": "thin_pool", "V": "thin", "s": "snapshot", "-": "linear"}.get(attr[:1], "other")


def logical_volumes(vg, node=None):
    """Every volume of the group, thin ones and thin pools included (libvirt lists only the thick ones)."""
    check_name(vg)
    out = _run(
        [
            "lvm",
            "lvs",
            "--reportformat",
            "json",
            "--units",
            "b",
            "--nosuffix",
            "-o",
            "lv_name,lv_size,lv_attr,pool_lv,data_percent,origin",
            vg,
        ],
        node,
    )
    rows = json.loads(out)["report"][0].get("lv", [])
    return [
        {
            "name": r["lv_name"],
            "size": int(r["lv_size"]),
            "kind": _kind(r["lv_attr"]),
            "thin_pool": r["pool_lv"] or None,
            "data_percent": float(r["data_percent"]) if r["data_percent"] else None,
            "origin": r["origin"] or None,
            "path": f"/dev/{vg}/{r['lv_name']}",
        }
        for r in rows
    ]


def refuse_removal_if_not_empty(vg, node=None):
    """A pool is removed only when its group holds no volume at all: thin volumes and thin pools are invisible to
    libvirt, which would call the pool empty and stop it under them."""
    volumes = logical_volumes(vg, node)
    if volumes:
        names = ", ".join(sorted(v["name"] for v in volumes))
        raise LvmError(f"The volume group '{vg}' still holds {len(volumes)} volume(s) ({names}): remove them first")


def extend(vg, lv, size_bytes, node=None):
    """Grow a volume to size_bytes (libvirt's logical pool cannot resize). Never shrinks."""
    check_name(vg)
    check_name(lv, "volume")
    current = next((v for v in logical_volumes(vg, node) if v["name"] == lv), None)
    if current is None:
        raise LvmError(f"Volume '{lv}' not found in '{vg}'")
    if size_bytes <= current["size"]:
        raise LvmError("A logical volume is only grown here: the new size must be larger")
    _run(["lvm", "lvextend", "-L", f"{int(size_bytes)}b", f"{vg}/{lv}"], node)
