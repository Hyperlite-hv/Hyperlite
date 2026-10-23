# Container snapshots (#424)

Proxmox VE takes snapshots of a container's root file system on ZFS, LVM-thin and Ceph. Hyperlite's containers live
in plain directories (`/var/lib/libvirt/containers/<name>`, or a `dir` storage pool: NFS, ZFS and iSCSI pools are
refused for containers), and libvirt's LXC driver has no snapshot at all (`virDomainSnapshotCreateXML` is not
supported, which is why container clones and backups already copy the directory). So a container snapshot here is a
copy of its directory, taken while it is stopped, as its clones and backups are.

## What was checked on a real host

rsync 3.2.7 on ext4, a 1.1 GB container file system (11,700 files) holding what a container can hold: a device node,
a FIFO, symbolic links, hard links, `user.` and `trusted.` extended attributes, a POSIX ACL, a file capability, a
setuid file, files owned by another user (100000), a 100 MB sparse file. Each copy was compared with a script that
reads every path's type, mode, owner, size, mtime (ns), device number, link target, every extended attribute, content
hash, holes and hard-link groups; it reports the copies `cp -r` and `rsync -a` without `-AX` make as different.

| What | Result |
|---|---|
| `rsync -aHAXS --numeric-ids` into an empty directory | Identical, 13 s |
| A second snapshot with `--link-dest=<the previous snapshot>` | Unchanged files are hard links to the previous snapshot's: it adds 8.5 MB |
| A file changed without changing its size or mtime, rsync's default quick check | **The snapshot keeps the old content**: rsync links the previous snapshot's file. With `--checksum`, identical |
| `--checksum` with a cold page cache | 4.7 s for the 1.1 GB tree |
| Restore: the snapshot copied into a new directory next to the container's, then swapped in | Identical to the snapshot, 13 s; the container's files share no inode with the snapshot, and writing into them leaves the snapshot unchanged |
| Deleting a snapshot whose files the next one links to | The next one is unchanged |

### A real container, started again

The lab's own cgroup layout (v1 and v2 mixed) makes libvirt_lxc fail, so this ran in a virtual machine of the lab: an
Ubuntu 24.04 system (Debian's archive was not reachable from the lab), its own systemd and a pure cgroup v2 tree as a
Debian 13 node has, Linux 6.8, libvirt 10.0, QEMU without KVM. Its container was a copy of that system, systemd as
PID 1, defined by Hyperlite's `build_container_xml` (without its network interface: snapshots do not involve it),
and a unit inside it wrote a line with the content of `/etc/hl-state` at each boot. Snapshots and restores were
Hyperlite's own `container_snapshots` functions.

| What | Result |
|---|---|
| The container started, its unit ran, then stopped | Booted in 10 s, `boot v1` |
| A snapshot, then `/etc/hl-state` set to `v2`, `/etc/os-release` removed, a file added, the container started again | `boot v2` |
| The snapshot restored | `v1` back, `/etc/os-release` back, the added file gone |
| The restored container started | Booted in 8 s, `boot v1`; its boot log is the snapshot's line and this boot's, no `v2` boot left |
| A second snapshot | The first took 526 MB, the second added 10 MB |

## Design

- **Where**: `<the directory holding the container's>/.snapshots/<container>/<snapshot>/`, root only (0700): a
  `rootfs/` copy and `snapshot.json` (name, description, date). On the container's own file system, so a snapshot can
  link to the previous one and a restore is a rename. Container names have no dot: no container can be named
  `.snapshots`, nor `<name>.restoring`.
- **Take** (container stopped): `rsync -aHAXS --numeric-ids --checksum --link-dest=<latest snapshot>` into
  `<snapshot>.partial`, renamed once complete; a failure removes it. The first snapshot is a full copy and is refused
  when the file system lacks the room for it.
- **Restore** (container stopped): the snapshot is copied (never linked: the container would write into the
  snapshot's files) into `<container>.restoring`, then swapped with the container's directory, which is removed. A
  failure leaves the container's directory as it was. Refused without room for a full copy.
- **Delete**: the snapshot's directory goes; the others keep their own links.
- **One operation at a time**: a snapshot being taken, restored or deleted makes start, rename, clone, backup and
  deletion of that container answer 409, and the reverse.
- **The container's life**: deleting it deletes its snapshots; renaming it renames their directory; a clone and a
  backup take its file system only.
- **API**: `GET/POST /containers/{name}/snapshots`, `POST /containers/{name}/snapshots/{snapshot}/restore` (tasks,
  202), `DELETE /containers/{name}/snapshots/{snapshot}`; administrators, as container clones and backups.
- **Package**: depends on `rsync`.
