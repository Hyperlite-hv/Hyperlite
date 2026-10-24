"""LVM storage pools (docs/design/lvm-storage.md): libvirt's `logical` pool, and what it does not do, read from LVM
itself.

Checked on a real volume group (libvirt 10.0, LVM 2.03): libvirt builds a volume group on an empty disk and makes
thick volumes in it, but it cannot resize one ("storage pool does not support changing of volume capacity"), and it
neither lists nor deletes thin volumes or their thin pool. Its pool deletion (`virStoragePoolDelete`) removes the
whole volume group, thin volumes included: Hyperlite never calls it, and removes a pool only while `lvs` shows no
volume in it at all.

LVM-thin is Hyperlite's own, through `lvm` (docs/design/lvm-storage.md): a pool's thin pool carries the THIN_TAG tag;
VM disks are thin volumes in it, snapshots thin snapshots named SNAP_PREFIX<volume>_<snapshot>. VM and snapshot names
hold letters, digits and dashes only, so the underscores split such a name without ambiguity.
"""

import json
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime

import libvirt

TIMEOUT_S = 60
# LVM's own rule for volume group and volume names, without the characters a shell or a path would read.
LVM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,126}$")
# The tag on the thin pool a Hyperlite pool makes its VM disks in.
THIN_TAG = "hyperlite_thin"
SNAP_PREFIX = "snap_"
# The share of the group's free space a new thin pool takes: LVM keeps room for a spare copy of its metadata.
THIN_POOL_SHARE = "95%FREE"


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
            "lv_name,lv_size,lv_attr,pool_lv,data_percent,origin,lv_tags,lv_time",
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
            "tags": [t for t in r.get("lv_tags", "").split(",") if t],
            "created_at": r.get("lv_time") or None,
            "path": f"/dev/{vg}/{r['lv_name']}",
        }
        for r in rows
    ]


def refuse_removal_if_not_empty(vg, node=None):
    """A pool is removed only when its group holds no volume at all: thin volumes and thin pools are invisible to
    libvirt, which would call the pool empty and stop it under them. The pool's own thin pool, once empty, holds no
    data and stays in the group (a pool's removal never removes anything from it)."""
    volumes = [v for v in logical_volumes(vg, node) if not _is_hyperlite_thin_pool(v)]
    if volumes:
        names = ", ".join(sorted(v["name"] for v in volumes))
        raise LvmError(f"The volume group '{vg}' still holds {len(volumes)} volume(s) ({names}): remove them first")


def _is_hyperlite_thin_pool(volume):
    return volume["kind"] == "thin_pool" and THIN_TAG in volume["tags"]


def group_of(pool):
    """The volume group of a libvirt logical pool."""
    return ET.fromstring(pool.XMLDesc(0)).findtext("source/name")


def thin_pool_of(vg, node=None):
    """The thin pool of the group a Hyperlite pool makes VM disks in, or None (thick volumes, through libvirt)."""
    return thin_pool_in(logical_volumes(vg, node))


def thin_pool_in(volumes):
    """The Hyperlite thin pool among a group's volumes (logical_volumes), or None."""
    return next((v for v in volumes if _is_hyperlite_thin_pool(v)), None)


def ensure_thin_pool(vg, name, node=None):
    """Make `name` the group's thin pool for VM disks: an existing thin pool is adopted, else one is created over the
    group's free space. One per group: a second, under another name, is refused."""
    check_name(vg)
    check_name(name, "thin pool")
    volumes = logical_volumes(vg, node)
    current = next((v for v in volumes if _is_hyperlite_thin_pool(v)), None)
    if current is not None and current["name"] != name:
        raise LvmError(f"The volume group '{vg}' already has its thin pool '{current['name']}'")
    existing = next((v for v in volumes if v["name"] == name), None)
    if existing is None:
        _run(
            [
                "lvm",
                "lvcreate",
                "-y",
                "--type",
                "thin-pool",
                "-l",
                THIN_POOL_SHARE,
                "--addtag",
                THIN_TAG,
                "-n",
                name,
                vg,
            ],
            node,
        )
    elif existing["kind"] != "thin_pool":
        raise LvmError(f"'{name}' is a volume of '{vg}', not a thin pool")
    elif current is None:
        _run(["lvm", "lvchange", "--addtag", THIN_TAG, f"{vg}/{name}"], node)


def thin_disks(paths, node=None):
    """(group, volume) of each path when every one is a thin volume, else None (a thick or unknown volume among
    them: no LVM snapshot for this VM)."""
    found = [_vg_lv(p) for p in paths]
    if not found or None in found:
        return None
    kinds = {}
    for vg in {vg for vg, _lv in found}:
        kinds.update({(vg, v["name"]): v["kind"] for v in logical_volumes(vg, node)})
    return found if all(kinds.get(d) == "thin" for d in found) else None


def _snap_name(lv, snapshot):
    return f"{SNAP_PREFIX}{lv}_{snapshot}"


def list_snapshots(disks, node=None):
    """A VM's snapshots, read from its first disk's group (all its disks are snapshotted together): name and
    creation time."""
    vg, lv = disks[0]
    prefix = _snap_name(lv, "")
    return [
        {"name": v["name"][len(prefix) :], "created_at": _epoch(v["created_at"])}
        for v in logical_volumes(vg, node)
        if v["name"].startswith(prefix) and v["kind"] == "thin"
    ]


def _epoch(lv_time):
    """LVM's lv_time ("2026-10-23 10:51:28 +0000") as seconds since the epoch, as ZFS snapshots give their time."""
    try:
        return str(int(datetime.strptime(lv_time, "%Y-%m-%d %H:%M:%S %z").timestamp()))
    except (TypeError, ValueError):
        return None


def snapshot(disks, snapshot_name, node=None):
    """A thin snapshot of each disk; one that fails removes those already taken. The caller pauses a running VM with
    several disks, so that all of them show the same instant."""
    taken_by_vg = {}
    for vg, lv in disks:
        taken_by_vg.setdefault(vg, {v["name"] for v in logical_volumes(vg, node)})
        if _snap_name(lv, snapshot_name) in taken_by_vg[vg]:
            raise LvmError(f"A snapshot '{snapshot_name}' already exists for '{vg}/{lv}'")
    done = []
    try:
        for vg, lv in disks:
            _run(["lvm", "lvcreate", "-y", "-s", "-n", _snap_name(lv, snapshot_name), f"{vg}/{lv}"], node)
            done.append((vg, lv))
    except LvmError:
        for vg, lv in done:
            _run(["lvm", "lvremove", "-y", f"{vg}/{_snap_name(lv, snapshot_name)}"], node)
        raise


def rollback(disks, snapshot_name, node=None):
    """Bring each disk (VM stopped) back to the snapshot. The snapshot stays: each disk becomes a new thin snapshot
    of it, as Proxmox VE does, so every snapshot remains a point the VM can go back to (unlike `lvconvert --merge`,
    which consumes it). The old volume is kept aside until every disk is done, and put back if one fails."""
    for vg, lv in disks:
        if not any(v["name"] == _snap_name(lv, snapshot_name) for v in logical_volumes(vg, node)):
            raise LvmError(f"Snapshot '{snapshot_name}' not found for '{vg}/{lv}'")
    aside = []
    try:
        for vg, lv in disks:
            _run(["lvm", "lvrename", f"{vg}/{lv}", f"{vg}/{lv}_rollback"], node)
            aside.append((vg, lv))
            # -kn: a snapshot is skipped at activation by default; this one is the VM's disk again.
            _run(["lvm", "lvcreate", "-y", "-s", "-kn", "-n", lv, f"{vg}/{_snap_name(lv, snapshot_name)}"], node)
    except LvmError:
        for vg, lv in aside:
            if any(v["name"] == lv for v in logical_volumes(vg, node)):
                _run(["lvm", "lvremove", "-y", f"{vg}/{lv}"], node)
            _run(["lvm", "lvrename", f"{vg}/{lv}_rollback", f"{vg}/{lv}"], node)
        raise
    for vg, lv in aside:
        _run(["lvm", "lvremove", "-y", f"{vg}/{lv}_rollback"], node)


def delete_snapshot(disks, snapshot_name, node=None):
    """Remove the snapshot on each disk; a disk without it (added to the VM later) is skipped."""
    for vg, lv in disks:
        name = _snap_name(lv, snapshot_name)
        if any(v["name"] == name for v in logical_volumes(vg, node)):
            _run(["lvm", "lvremove", "-y", f"{vg}/{name}"], node)


def extend(vg, lv, size_bytes, node=None):
    """Grow a volume to size_bytes (libvirt's logical pool cannot resize). Never shrinks."""
    check_name(vg)
    check_name(lv, "volume")
    current = next((v for v in logical_volumes(vg, node) if v["name"] == lv), None)
    if current is None:
        raise LvmError(f"Volume '{lv}' not found in '{vg}'")
    if size_bytes <= current["size"]:
        raise LvmError("A logical volume is only grown here: the new size must be larger")
    _run(["lvm", "lvextend", "-L", f"{-(-int(size_bytes) // 2**20)}m", f"{vg}/{lv}"], node)


def _vg_lv(path):
    """(group, volume) when `path` is shaped as a logical volume's (/dev/<group>/<volume>), else None."""
    parts = str(path).split("/")
    if len(parts) != 4 or parts[:2] != ["", "dev"] or parts[2] in ("zvol", "disk", "mapper"):
        return None
    if not LVM_NAME_RE.fullmatch(parts[2]) or not LVM_NAME_RE.fullmatch(parts[3]):
        return None
    return parts[2], parts[3]


def on_lvm(root):
    """Whether a domain's XML has a block disk shaped as a logical volume: read from the XML alone, for the VM list,
    where an `lvm` call per VM would cost too much."""
    return any(_vg_lv(d.get("dev", "")) for d in root.findall(".//devices/disk[@type='block'][@device='disk']/source"))


def lv_of_path(path, node=None):
    """(group, volume) when `path` is a volume of a volume group (/dev/<group>/<volume>), else None."""
    found = _vg_lv(path)
    if found is None:
        return None
    vg, lv = found
    try:
        names = {v["name"] for v in logical_volumes(vg, node)}
    except LvmError:
        return None
    return (vg, lv) if lv in names else None


def _logical_pool_groups(conn):
    return {
        ET.fromstring(p.XMLDesc(0)).findtext("source/name")
        for p in conn.listAllStoragePools(0)
        if ET.fromstring(p.XMLDesc(0)).get("type") == "logical"
    }


def delete_vm_volume(conn, path, node=None):
    """Delete a deleted VM's block disk when it is a volume of a logical pool: a thick one through libvirt, a thin one
    (which libvirt does not see) with its snapshots through `lvm`. Anything else (an iSCSI LUN, a volume of a group no
    Hyperlite pool manages) is left alone. True when deleted. libvirtError and LvmError propagate."""
    try:
        vol = conn.storageVolLookupByPath(path)
    except libvirt.libvirtError:
        vol = None
    if vol is not None:
        if ET.fromstring(vol.storagePoolLookupByVolume().XMLDesc(0)).get("type") != "logical":
            return False
        vol.delete(0)
        return True
    found = _vg_lv(path)
    if found is None or found[0] not in _logical_pool_groups(conn):
        return False
    vg, lv = found
    volumes = logical_volumes(vg, node)
    if not any(v["name"] == lv and v["kind"] == "thin" for v in volumes):
        return False
    prefix = _snap_name(lv, "")
    for v in volumes:
        if v["name"].startswith(prefix) and v["kind"] == "thin":
            _run(["lvm", "lvremove", "-y", f"{vg}/{v['name']}"], node)
    _run(["lvm", "lvremove", "-y", f"{vg}/{lv}"], node)
    return True


def create_vm_disk(pool, vm_name, index, disk_gb, base_image=None, import_source=None, thin_pool=None):
    """A VM disk on a libvirt logical pool, as create_zvol_disk does on ZFS: a thick volume made through libvirt (or a
    thin volume in `thin_pool`, through `lvm`), the base cloud image or an imported disk written into it raw. Returns
    its device path, attached as a block disk. A failure removes the volume."""
    name = vm_name if index == 0 else f"{vm_name}-{index + 1}"
    check_name(name, "volume")
    source = import_source or base_image
    if import_source is not None:
        info = subprocess.run(
            ["qemu-img", "info", "--output=json", str(import_source)], check=True, capture_output=True, text=True
        )
        disk_gb = max(1, -(-json.loads(info.stdout)["virtual-size"] // 2**30))
    if thin_pool is not None:
        return _create_thin_disk(group_of(pool), thin_pool, name, disk_gb, source)
    vol = pool.createXML(f"<volume><name>{name}</name><capacity unit='GiB'>{int(disk_gb)}</capacity></volume>", 0)
    try:
        if source is not None:
            subprocess.run(
                ["qemu-img", "convert", "-O", "raw", str(source), vol.path()],
                check=True,
                capture_output=True,
                text=True,
            )
    except BaseException:
        vol.delete(0)
        raise
    return vol.path()


def _create_thin_disk(vg, thin_pool, name, disk_gb, source):
    check_name(vg)
    _run(["lvm", "lvcreate", "-y", "-V", f"{int(disk_gb)}g", "--thinpool", f"{vg}/{thin_pool}", "-n", name], None)
    path = f"/dev/{vg}/{name}"
    try:
        if source is not None:
            # A new thin volume reads as zeros: --target-is-zero lets qemu-img skip the zeros of the image, which
            # would otherwise take room in the thin pool.
            subprocess.run(
                ["qemu-img", "convert", "-n", "--target-is-zero", "-O", "raw", str(source), path],
                check=True,
                capture_output=True,
                text=True,
            )
    except BaseException:
        _run(["lvm", "lvremove", "-y", f"{vg}/{name}"], None)
        raise
    return path
