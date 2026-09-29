"""Helper process of app/core/file_restore.py: opens a backup's disk images read-only with libguestfs and answers
requests read from stdin, one JSON object per line, with one JSON line on stdout.

It runs under the system Python (the one the distribution's python3-guestfs is built for), not the service's
virtual environment, so it imports nothing from Hyperlite. libguestfs reads the guest filesystems inside its own
small appliance VM: a damaged or hostile filesystem in a backup never reaches the host kernel, which mounting the
image on the host would expose.

Requests:
  {"op": "filesystems"}                              -> {"filesystems": [{device, type, size, label}]}
  {"op": "ls", "device": d, "path": p}               -> {"entries": [{name, type, size, mtime}]}
  {"op": "export", "device": d, "path": p, "dest": f} -> {"kind": "file" | "dir"}  (a directory as a .tar.gz)
"""

import json
import os
import sys

import guestfs

TYPES = {"d": "dir", "r": "file", "l": "link"}
SKIP_FS = {"swap", "unknown", ""}


def main(disks):
    g = guestfs.GuestFS(python_return_dict=True)
    for disk in disks:
        g.add_drive_opts(disk, readonly=1)
    g.launch()
    mounted = [None]

    def mount(device):
        if device not in {fs for fs, kind in g.list_filesystems().items() if kind not in SKIP_FS}:
            raise ValueError(f"No filesystem {device} in this backup")
        if mounted[0] != device:
            g.umount_all()
            g.mount_ro(device, "/")
            mounted[0] = device

    def check_path(path):
        if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or len(path) > 4096:
            raise ValueError("The path must be absolute")
        return path

    def filesystems():
        out = []
        for device, kind in sorted(g.list_filesystems().items()):
            if kind in SKIP_FS:
                continue
            try:
                label = g.vfs_label(device)
            except RuntimeError:
                label = ""
            try:
                size = g.blockdev_getsize64(device)
            except RuntimeError:
                size = None
            out.append({"device": device, "type": kind, "size": size, "label": label})
        return {"filesystems": out}

    def ls(device, path):
        mount(device)
        path = check_path(path)
        if not g.is_dir(path, followsymlinks=False):
            raise ValueError(f"{path} is not a directory")
        names = [d["name"] for d in g.readdir(path) if d["name"] not in (".", "..")]
        entries = []
        stats = g.lstatnslist(path, names) if names else []
        for name, st in zip(names, stats, strict=True):
            mode = st["st_mode"] & 0o170000
            kind = (
                "dir" if mode == 0o040000 else "link" if mode == 0o120000 else "file" if mode == 0o100000 else "other"
            )
            entries.append({"name": name, "type": kind, "size": st["st_size"], "mtime": st["st_mtime_sec"]})
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return {"entries": entries}

    def export(device, path, dest):
        mount(device)
        path = check_path(path)
        if g.is_dir(path, followsymlinks=False):
            g.tar_out(path, dest, compress="gzip")
            return {"kind": "dir"}
        if g.is_file(path, followsymlinks=False):
            g.download(path, dest)
            return {"kind": "file"}
        raise ValueError(f"{path} is neither a file nor a directory")

    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "filesystems":
                res = filesystems()
            elif op == "ls":
                res = ls(req["device"], req["path"])
            elif op == "export":
                res = export(req["device"], req["path"], req["dest"])
            elif op == "close":
                break
            else:
                res = {"error": f"Unknown request {op!r}"}
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            res = {"error": str(e)}
        print(json.dumps(res), flush=True)
    g.close()


if __name__ == "__main__":
    os.environ.setdefault("LIBGUESTFS_BACKEND", "direct")
    main(sys.argv[1:])
