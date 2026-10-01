# Two sites: recovery of a lost site

Nodes on two distant sites (linked over the Internet, through Tailscale or WireGuard for instance) cannot form one
cluster: Corosync needs LAN latencies (under 5 ms, see `docs/design/control-plane-v2-migration.md` section 15.2).
Each site therefore runs its own Hyperlite, and each one keeps working when the other is lost. This page explains how
the surviving site restarts the lost site's VMs.

## How it works

1. **Each site backs its VMs up to a storage of the other site**: an NFS share hosted there, reached over the link
   between the sites. The backups are ordinary Hyperlite backups (`<directory>/<VM>/<date>/`), each with its
   `manifest.json`, which names the host that made it.
2. **When a site is lost**, an administrator of the other site opens **Backups › Recovery of another site**, points it
   at that storage as this site mounts it, chooses the VMs and restores them. Each VM becomes a **new VM**, restored
   from its latest backup **after an integrity check**, under its own name or another one.

Nothing is overwritten, and nothing starts by itself. Data at risk: what changed since the last backup of each VM (a
day with daily backups).

## Setting it up

On each site, say A whose backups go to B:

1. **On site B**, export a directory over NFS to site A's address (a NAS or a node of B).
2. **On site A**, create an NFS storage pool on that export (*Storage › Add*, type NFS; choose the NFS version the
   server offers). Its mount point is `/var/lib/libvirt/hyperlite-pools/<pool name>`.
3. **On site A**, schedule backups to that directory with a grouped job (*Backups › Grouped jobs*, all the VMs of
   the node or those of a tag or a pool, target directory = the pool's mount point). A per-VM schedule can target it
   too, through the API (`PUT /vms/{name}/backup-schedule` with `cible_dir`).
4. **On site B**, make the same export readable: as a local directory when the NAS is on site B, or as an NFS pool.
5. Do the same in the other direction (B's backups on site A).
6. **Test it**: on site B, open *Backups › Recovery of another site*, enter the directory and press *Look for
   backups*. Every VM of site A must be listed with its latest backup. Restore one under another name (`web-test`)
   on an isolated network (the network choice of the card), start it, check it, delete it.

## Replication: losing 15 minutes instead of a day

Backups lose what changed since the last one. **Backups › Replication to another site** copies VMs to the same kind
of storage every few minutes (15 by default):

- each copy only holds what changed since the previous one (QEMU's dirty bitmaps, as Proxmox Backup Server uses);
  a full copy starts a new chain once a day, and the two newest chains are kept;
- the copies are laid out like backups, so the other site restores them with *Recovery of another site*: it reads
  the newest copy through its chain into an independent disk;
- a stopped VM is not copied again until it runs; when it ran since its last copy, it is started **paused** for the
  copy (the guest never runs) and stopped again, as Proxmox does for backups of stopped VMs;
- the card lists each VM's last copy and warns about the ones without a copy within twice the interval.

Limits:

- disks must be **qcow2 files** (version 3). ZFS, iSCSI and raw disks are listed with the reason and not copied;
- a VM with a passed-through device is never started paused: when it is stopped, it gets a full copy instead;
- while a VM is replicated, libvirt keeps a checkpoint on its disks. Moving or resizing a disk, reverting a snapshot,
  taking a snapshot of the stopped VM, renaming it or migrating it drop that checkpoint first, and the next copy is
  a full one. Hot backups of a replicated VM go through libvirt's backup API and keep the chain;
- the storage must let the host's root user and QEMU write: an NFS export with `root_squash` refuses QEMU's writes,
  use `no_root_squash` for this export, restricted to the other site's address;
- the first full copy of each VM crosses the link in full. On a slow link, start with one VM.

## The day a site is lost

1. **Make sure it is really down.** From site B, a dead site A and a cut link between the sites look the same. If
   site A still runs and you start its VMs on B, both copies serve users and write different data, which cannot be
   merged. Call someone on site A, check its power or its Internet access, or power it off.
2. On site B: *Backups › Recovery of another site*, the directory of site A's backups, *Look for backups*.
3. Choose the VMs. A VM whose name already exists on site B is unticked and gets `-recup` as a suggested name.
4. Choose the network of the restored VMs if site A's networks do not exist on site B.
5. *Restore*: the VMs are restored one after another; follow them in the task list. A VM that cannot be restored
   (corrupted backup, missing network) is named in the task's result, with the reason; the others are restored all
   the same.
6. Start the VMs, and update what points to them (DNS records, port forwards, VPN endpoints).

## When site A comes back

Site A's VMs start again on site A at its next boot when they were set to start at boot. **Before reconnecting site A,
stop them there or disconnect site A from the network**, decide which copy of each VM is kept (usually the one that
ran on site B since the loss), and move it back with a backup and restore or a live migration.

## Limits

- Data loss is bounded by the backup interval; storage replication between sites, every few minutes, is the next
  step (`docs/design/replication.md`).
- A backup that names a network missing on site B needs the network choice of step 4.
- The scan reads up to 2,000 VMs and their 500 most recent backups each, and never follows a symbolic link found on
  the share.
