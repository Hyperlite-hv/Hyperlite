# The cluster configuration as files in /etc/hyperlite (#525)

Status: **proposal** (2026-10-24). The choices of section 2 are the maintainer's; the code starts after the privilege
separation (#511), as decided on 2026-10-21 (`docs/design/privilege-separation.md`, section 11).

## 1. Goal

Proxmox VE's `pmxcfs` is a FUSE file system mounted at `/etc/pve`: an administrator reads the cluster's configuration
with `ls`, `cat` and `grep`, every node shows the same files, `/etc/pve/local` is the node's own directory and
`/etc/pve/priv` is root's. hyperlite-cfs holds the same kind of tree, but only behind its Unix socket: nothing shows
it as files, and the files of a node (its TLS certificate, its keys) live outside it, in `/etc/hyperlite`.

Done when: hyperlite-cfs mounts its tree at `/etc/hyperlite` on every node; `local` points at the node's own
directory; `priv/` and `nodes/<name>/priv/` are root's; the node's files that live in `/etc/hyperlite` today are in
the tree; it is tested under the sanitizers and fuzzing the socket has.

## 2. Decisions (maintainer, 2026-10-24)

| Question | Proxmox VE | vSphere | Hyperlite |
|---|---|---|---|
| What the files hold | Text formats of its own (`qemu-server/100.conf`, `user.cfg`) | No file view of the cluster | **The tree as it is**: one JSON document per object, the paths the code already writes (`db/<table>/<key>`), plus virtual files (section 4). No translation layer to keep right for every table |
| Writing through the files | Allowed (`vi /etc/pve/qemu-server/100.conf`), checked when read | Everything through the API | **Read-only.** A change goes through the API, the dashboard or `hyperlite-admin`, which check every value. A hand-written row the database refuses would stop the apply of every change on every node (`inbound.py`) until repaired by hand |
| A node's own files | In the tree: `nodes/<name>/` (`pve-ssl.key`, `lrm_status`...), `/etc/pve/local` pointing at it | Kept by each host | **In the tree**, as Proxmox: `nodes/<name>/`, `/etc/hyperlite/local` pointing at it. Root of any node can read every node's files, as on Proxmox |

## 3. What /etc/hyperlite shows

The tree's paths, unchanged, under the mount point:

| Path | What | Owner, mode |
|---|---|---|
| `db/<table>/<key>` | a row of a replicated table, JSON (`app/repositories/cfs/tables.py`) | root:www-data, 0640 |
| `cluster/...` | members, Corosync's settings, certificate fingerprints, update state (`cluster_setup.py`, `peer_https.py`) | root:www-data, 0640 |
| `nodes/<name>/` | the node's own files (section 5) | root:www-data, 0640 |
| `nodes/<name>/priv/` | the node's secrets | root:root, 0600 (directories 0700) |
| `priv/` | the cluster's secrets (`priv/db/` rows, `priv/cluster/authkey`) | root:root, 0600 (directories 0700) |
| `local` | a symbolic link to `nodes/<this node>` | |
| `.members`, `.version`, `.quorum` | virtual files (section 4) | root:www-data, 0444 |

`www-data` is the API's account after #511, which reads the configuration it needs as `pveproxy` reads `/etc/pve`:
the same rule the socket applies to `priv/` (refused to every client but root), now through the kernel's credentials
of the reading process instead of `SO_PEERCRED`. Directories are listed from the tree (`cfs_store_list`); a file's
modification time is the entry's `mtime`, its size its length; there are no other owners, links or special files.

Every write (`write`, `create`, `mkdir`, `unlink`, `rename`, `chmod`, `setxattr`...) is refused with `EROFS`, root
included.

## 4. Virtual files

Generated when read, never stored, as Proxmox's `.members`, `.version` and `.vmlist`:

- **`.members`**: JSON, the cluster's name, this node's id, and for each member its name, id, address and whether it
  is online: the names and addresses from the tree's `cluster/corosync.json`, online from the membership the daemon
  follows (its members' Corosync ids). Outside a cluster: this node alone.
- **`.version`**: JSON, the tree's version and checksum, the same as `hyperlite-cfs status` gives: two nodes with the
  same pair hold the same tree.
- **`.quorum`**: `1` when this node may change the configuration, `0` when it is read-only (no quorum, or
  synchronising), the same answer the API's read-only mode reads.

No `.vmlist`: where each VM runs is the status channel's (`hyperlite-statd`), not the tree's, and `GET /vms` already
gives it. Others can be added later; a script reads them with `jq`.

## 5. The node's files move into the tree

| Today | In the tree | Who reads it |
|---|---|---|
| `/etc/hyperlite/local/hyperlite.crt`, `openssl-hyperlite.cnf` | `nodes/<name>/hyperlite.crt`, `openssl-hyperlite.cnf` | the API (HTTPS) |
| `/etc/hyperlite/local/hyperlite.key` | `nodes/<name>/hyperlite.key`, root:www-data 0640 (as `pve-ssl.key`) | the API, which terminates HTTPS |
| `/etc/hyperlite/priv/env`, `/etc/hyperlite/priv/ssh/` | `nodes/<name>/priv/` | the daemon (root) |
| `/etc/hyperlite/priv/authkey.key`, `/etc/hyperlite/authkeys.json` | wherever #511 settles the session keys (`priv/`, cluster-wide as Proxmox's `authkey.key`, is the expected place) | the daemon signs, the API verifies |

Hyperlite reads and writes them through the socket, as every other entry: the mount is for people and scripts. Each
file stays within the tree's limits (1 MiB a file, 128 MiB the tree).

- **At the update** that brings the mount, the unit's `ExecStartPre` copies each file into the tree (create only: one
  already there is kept), checks it reads back the same, then moves the old directory aside
  (`/var/lib/hyperlite/etc-before-cfs-<date>`). It never mounts over a directory that still holds files.
- **When a node joins a cluster** its tree is replaced by the cluster's (`cluster_setup.join`): its own
  `nodes/<name>/` is read before and written again after, before Hyperlite restarts. Without that step a joined node
  would lose its certificate and keys.
- **When a member is removed** (`cluster_setup.remove_member`), its `nodes/<name>/` is deleted with it: `pvecm delnode`
  leaves that to the administrator, Hyperlite does it, the node is gone.

## 6. How the daemon serves it

- **libfuse3's low-level interface, in the daemon's own `poll()` loop** (`server.c`): the session's file descriptor is
  one more source, each request is read and answered from the store on that thread. No thread, no lock: a read sees
  the tree as the last applied change left it, never half of one.
- Reads go to `cfs_store_get`/`cfs_store_list`, which serve the socket; the virtual files to what `CFS_OP_STATUS`
  answers. The kernel caches nothing (`direct_io`, entry and attribute timeouts of 0): a change applied on any node
  shows at once on every node.
- `allow_other` (every account may read what its mode lets it), `default_permissions` (the kernel checks modes
  itself), no `suid` or `dev`.
- The daemon never reads its own mount. Hyperlite's processes read the configuration through the socket, so a slow
  read of `/etc/hyperlite` by a person or a script never slows them.
- **Stopping**: the daemon unmounts at exit (`fuse_session_unmount`); a stale mount left by a crash is cleaned by the
  unit's `ExecStartPre` (`fusermount3 -u -z`) before the next start.

## 7. hyperlite-cfs runs on every node

Today its unit is installed **disabled** and runs only on a node of a cluster (or in shadow mode). Since the files of
every node live in the tree, it must run everywhere, as `pve-cluster` does on a single Proxmox node:

- the package enables `hyperlite-cfs.service` in local mode on every node; a cluster switches it to `--cluster` as
  today;
- `hyperlite.service` and `hyperlite-daemon.service` start after it (`Requires=` and `After=`): without the daemon
  there is no certificate to serve HTTPS with, as Proxmox's web interface does not start without `pmxcfs`;
- a node that is not in a cluster then copies its configuration into its own daemon too (local mode, as in shadow
  mode): its `db/` shows the same files a cluster's does.

## 8. The update and the return

The same trap as in `docs/design/privilege-separation.md`, section 9: the version installed runs the update. The
version that moves the node's files into the tree is installed over one that knows how to find them there:

1. **the bridge** (moves nothing): reads the node's files from the tree when `/etc/hyperlite` holds none, and its
   rollback moves them back out (`ExecStartPre`, from the kept directory or from the tree);
2. **the move**: refuses to install over a version older than the bridge (`preinst`), mounts, moves the files in. A
   failure at start puts them back and fails, which the watchdog turns into a rollback.

In a cluster the update is rolling: a member still on the bridge reads its own files from its old directory, the
members already moved from the tree, and the tree only gains `nodes/<name>/` entries no older version reads.

## 9. Packaging

- Build: `libfuse3-dev` in CI's build of the daemon. Runtime: `fuse3` (Debian 13: `fusermount3` and `libfuse3-4`), a
  dependency of the package; `/dev/fuse` exists on every Debian kernel.
- Nothing else ships: no Perl, no extra service.

## 10. Tests

- **C**, under ASan and UBSan as the socket's: every operation of the mount (lookup, getattr, readdir, open, read,
  readlink) against a store, through libfuse3's low-level request decoding, without a kernel; the modes and the
  refusals for `priv/`; every write refused with `EROFS`; the virtual files.
- **Fuzzing**: a `fuzz_fuse` target feeding raw FUSE requests (`fuse_session_process_buf`), as `fuzz_request` feeds
  the socket's.
- **Python** (`cfs/tests/python`): the daemon mounted in a temporary directory (root, as the cluster tests): `ls`,
  `cat`, a change through the socket seen at once, `priv/` refused to another account, `EROFS`, the unmount at stop
  and after a `kill -9`.
- **Cluster** (`cfs/tests/cluster`, three nodes on Corosync): a change on one node read through the mount of the two
  others; `.members` and `.quorum` after a node is cut off.
- **Lab** (`lab/tests`, three real nodes from the package): a fresh installation, an update from the bridge (the
  node's files moved, HTTPS answering with the same certificate), a join (the joined node keeps its certificate), a
  return to the bridge (the files back where it reads them).

## 11. Steps

Each one ships with its tests:

1. the mount, read-only, with the virtual files, enabled on every node (sections 3, 4, 6, 7), the node's files still
   where they are;
2. the bridge (section 8, 1);
3. the node's files into the tree, the join and the removal (section 5), and the move (section 8, 2).

## 12. Risks

- **A daemon that hangs blocks whoever reads `/etc/hyperlite`**, as with `/etc/pve`. Hyperlite itself reads through
  the socket, with timeouts; the mount only blocks people and scripts.
- **The daemon must start for the node to serve HTTPS** (section 7). It is already required in a cluster; on a single
  node it is new. Its start does not depend on Corosync in local mode.
- **Root on any node reads every node's keys** (decided, as Proxmox). A node removed from the cluster loses its copy
  with its tree.
