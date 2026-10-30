"""Read a backup out of a backup store with nothing but the store and a key: after the loss of every node and of the
cluster's secrets (docs/design/backup-store.md, section 6). Runs on any machine with Hyperlite's package, the
service not needed:

    python3 -m app.core.backup_store_recover list STORE
    python3 -m app.core.backup_store_recover disk STORE VM_UUID TIME DISK OUT.raw --master-key master.pem
    python3 -m app.core.backup_store_recover disk STORE VM_UUID TIME DISK OUT.raw --key-code XXXX-XXXX-...
    python3 -m app.core.backup_store_recover file STORE VM_UUID TIME NAME OUT --master-key master.pem

`disk` writes a raw image, sparse: `qemu-img convert -f raw -O qcow2 OUT.raw disk.qcow2` makes a qcow2 of it. `file`
writes one of the files kept with the backup (vm-config.json, domain.xml, the NVRAM, the TPM state).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from app.core import backup_store


def _store(root, args, manifest=None):
    config = json.loads((Path(root) / backup_store.CONFIG).read_text())
    if not config.get("encrypted"):
        return backup_store.Store(root)
    if args.key_code:
        return backup_store.Store(root, backup_store.key_from_code(args.key_code))
    if args.master_key:
        if manifest is None:
            raise backup_store.StoreError("The master key opens one backup at a time: name it")
        return backup_store.Store(root, backup_store.recover_key(manifest, Path(args.master_key).read_bytes()))
    raise backup_store.StoreError("This store is encrypted: give --key-code or --master-key")


def write_raw(store, index, out):
    """The disk as a raw image at `out`, its zero chunks left as holes."""
    with open(out, "wb") as f:
        f.truncate(index.size)
        for i, digest in enumerate(index.digests):
            if digest == backup_store.ZERO:
                continue
            offset = i * index.chunk_size
            f.seek(offset)
            f.write(store.get(digest, min(index.chunk_size, index.size - offset)))
        f.flush()
        os.fsync(f.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m app.core.backup_store_recover", description=__doc__.split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("store")
    for name in ("disk", "file"):
        p = sub.add_parser(name)
        for arg in ("store", "vm_uuid", "time", "name", "out"):
            p.add_argument(arg)
        p.add_argument("--key-code", help="the store's key, as Hyperlite showed it")
        p.add_argument("--master-key", help="the master recovery key's private half (PEM)")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "list":
            config = json.loads((Path(args.store) / backup_store.CONFIG).read_text())
            base = Path(args.store) / "vm"
            for vm in sorted(base.iterdir()) if base.is_dir() else []:
                for backup in sorted(vm.iterdir()):
                    try:
                        manifest = json.loads((backup / "manifest.json").read_text())
                    except (OSError, ValueError):
                        continue
                    sizes = ", ".join(f"{d} {v['size'] / 1024**3:.1f} GiB" for d, v in manifest["disks"].items())
                    recovery = " (master key)" if manifest.get("recovery_key") else ""
                    print(f"{vm.name} {backup.name} {manifest.get('vm', '?')}: {sizes}{recovery}")
            print(f"store {config['id']}, {'encrypted' if config.get('encrypted') else 'not encrypted'}")
            return 0
        directory = Path(args.store) / "vm" / args.vm_uuid / args.time
        manifest = json.loads((directory / "manifest.json").read_text())
        store = _store(args.store, args, manifest)
        if args.cmd == "disk":
            write_raw(store, store.index_of(args.vm_uuid, args.time, args.name), args.out)
        else:
            Path(args.out).write_bytes(store.read_file(args.vm_uuid, args.time, args.name))
    except (
        OSError,
        ValueError,
        KeyError,
        backup_store.StoreError,
        backup_store.DamagedChunk,
        backup_store.MissingChunk,
    ) as e:
        print(f"Not recovered: {getattr(e, 'message', None) or e}", file=sys.stderr)
        return 1
    print(f"Written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
