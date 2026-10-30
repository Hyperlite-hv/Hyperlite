# Reading a backup store without its cluster

A backup store (`docs/design/backup-store.md`) holds complete images: any backup is read alone, with no other backup.
This page is for the worst case, when the cluster that made the backups is gone, its secrets with it, and only the
store's directory is left (a NAS, a disk taken elsewhere).

## Taking the store over in a new cluster

The usual way: install Hyperlite, add a storage pool that reaches the store's directory, then **Backups › Backup stores
› Add a store** on that pool and directory. Hyperlite finds the store already there and takes it over, its backups
listed. An **encrypted** store asks for **its key's code** (the one Hyperlite showed once when it was made), in **Key of
the store already there**.

## Without Hyperlite running: the recovery tool

On any machine with Hyperlite's package (the service does not need to run), as root:

```bash
cd /usr/lib/hyperlite
venv/bin/python -m app.core.backup_store_recover list /mnt/nas/hyperlite-store
venv/bin/python -m app.core.backup_store_recover disk /mnt/nas/hyperlite-store VM_UUID TIME vda /var/tmp/vda.raw --key-code XXXX-XXXX-...
venv/bin/python -m app.core.backup_store_recover file /mnt/nas/hyperlite-store VM_UUID TIME vm-config.json /var/tmp/vm-config.json --key-code XXXX-XXXX-...
qemu-img convert -f raw -O qcow2 /var/tmp/vda.raw /var/lib/libvirt/images/restored.qcow2
```

- `list` shows each backup: the VM's id and the backup's time (the two names `disk` and `file` take), its VM name, its
  disks and their sizes, and whether it carries a master recovery key.
- `disk` writes one disk as a raw image, sparse (its zero chunks left as holes); `qemu-img convert` makes a qcow2 of it.
- `file` writes one of the files kept with the backup: `vm-config.json` (processors, memory, network, firmware),
  `domain.xml` (the VM's whole definition), `nvram.fd` and `tpm.tar` (a UEFI VM's variables and TPM state).
- **An encrypted store** needs one of its keys:
  - `--key-code`: the store's key, as Hyperlite showed it;
  - `--master-key master.pem`: the master recovery key's private half, when the store was made with one. It opens
    every backup made since, each carrying the store's key sealed with the master key's public half.

Every chunk read is checked against its digest (and its tag when encrypted): a damaged one stops the tool with an error
rather than writing a wrong disk.

## What to keep, and where

- The **store key** and the **master recovery key** are shown once. Keep them outside the cluster: a password manager,
  a printed copy in a safe. Lose both, and the cluster with them, and an encrypted store cannot be read by anyone.
- An unencrypted store needs nothing but its directory.
