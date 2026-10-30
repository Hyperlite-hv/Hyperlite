# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions published to the APT repository were timestamp based (`YYYY.MM.DD.HHMM`) until 1.0.0; from 1.0.0 they follow [Semantic Versioning](https://semver.org/): the release PR sets `VERSION`, renames `[Unreleased]` to the version, and adds its French notes in `docs/release-notes/fr/<version>.md`. The section of each version is the release note shown before updating (see `docs/design/updates-1.0.md`).

## [Unreleased]

## [1.4.0] - 2026-10-30

### Added

- **A node with a BMC is powered off through it before its HA VMs are recovered** (#333, step 2): the manual recovery of a protected VM powers its node off through IPMI or Redfish (`fence_ipmilan`, `fence_redfish`), reads its power state until it says off (60 s at most), and only then restarts the VM elsewhere; without that confirmation the recovery is refused, as the old copy may still run. Never a reboot. A node without a BMC keeps the SSH attempt as before. The package recommends `fence-agents-ipmilan` and `fence-agents-redfish` (about 120 KB, not the 30 MB of every agent). The AMT method stays unavailable: Debian 13 packages no `fence_amt_ws`, and the message now says so. An IPMI password longer than 20 bytes is refused when it is saved: `ipmitool` would refuse it when the fence is needed.

- **HA only on a share that locks across nodes** (#333, step 3): QEMU's own image locks refuse a disk that another node runs, which the lab checked on NFS 4.2 (released once the VM stops, or 94 s after its node dies). A share mounted so that its locks stay on the node that took them (NFS `nolock` or `local_lock=` other than `none`, SMB `nobrl`) protects nothing between nodes: turning HA on refuses a VM with a disk there, or with QEMU's locks turned off in its definition, and the node that runs a protected VM turns its protection off with an audit entry if that changes later.

- **HA admission control** (#333, step 5), as vSphere's: the loss of any one member must leave room for its running HA VMs on the others, placed the largest first on the member with the most free memory left (each node publishes its free memory). The cluster's policy (`PUT /ha/admission`): `warn`, the default, accepts and says which member could not be absorbed (in the answer of turning HA on, in the service's log when an HA VM starts, and in `GET /ha/status`); `strict` refuses to turn HA on for a running VM, or to start an HA VM, that would leave a member unabsorbed; `off` checks nothing. A member whose free memory is not known (a node before this version) is named and never refused for.

- The HA page of a cluster (#333, step 7): automatic HA's state, each member's state (seen, its watchdog, its free memory), admission control's policy and result, the HA history, and each HA VM's priority, preferred nodes and monitoring, changed in place (`PUT /ha/{vm}/policy`).

- **Automatic HA in a cluster** (#333, step 4): the leader, with the quorum, restarts the HA VMs of a member whose status is older than 60 s once it is known to be stopped: its BMC powered it off and read it off, or its watchdog, armed when it was last seen, has had its reset delay plus 30 s. Only the HA VMs that were running in the member's last status are restarted (one stopped on purpose stays stopped, as Proxmox's requested state). The VMs restart one by one, by priority (`high`, `medium`, `low`), on their first preferred node that can take them, else on the online member with the most free memory, never on one in maintenance; a disk still locked by the old node's storage lease is tried again for two minutes; at most 3 automatic restarts of a VM per hour, then it is left to an administrator. With no proof that the member stopped, its VMs are left to an administrator too. Every step is the VM's HA state (the same on every node), an audit entry and a notification. The node that comes back undefines the VMs that moved before anything starts at boot, and HA VMs no longer use libvirt's own autostart. `POST /ha/{vm}/enable` takes `priority` and `preferred_nodes`. The dry-run watcher is now for hosts driven over SSH only.

- VM monitoring of HA VMs (#333, step 6), off by default per VM (`monitoring` when HA is turned on, or `PUT /ha/{vm}/monitoring`): a guest whose agent answered before, then stays silent for three rounds of 30 s with no disk or network I/O, is reset in place by its node, at most three times an hour, then left to an administrator. It needs qemu-guest-agent in the guest.

- Load balancing applies its moves (#334, step 4): automatic by default, two live migrations at a time in the cluster and one per member, paused after two failures in a row until the settings are saved again; manual mode, per-VM exceptions (manual, excluded), its API (/drs), and an administrator's start or migration refused when it breaks a "must" rule.

- Load balancing's engine (#334, step 3): the leader reads every member's load from its status, finds an imbalance that lasts 15 minutes (or a member above 90 %, or a broken rule) and computes the moves that even it, the rules kept; only VMs whose disks are all on a shared pool move.

- Each member's status carries its load and its VMs' (CPU, memory), as the metrics collector last measured them, for load balancing (#334, step 1).

- Load balancing's rules (#334, step 2): VMs kept together, apart, on some nodes or off them, as a "must" or a "should", and its settings (mode, threshold: automatic, level 3 by default); HA restarts a VM only where it keeps its "must" rules, else leaves it to an administrator, saying why.

- Incremental backups into a backup store (#337, step 2 of `docs/design/backup-store.md`): a running VM's qcow2
  disks keep a dirty bitmap from one backup to the next, and only the 4 MiB chunks with a changed block are read
  again (measured on a 200 MB disk: 50 chunks read for the first backup, 3 after 10 MiB written, 0 with no change).
  Each backup stays a complete image. Raw files and block devices are read whole, their unchanged chunks still stored
  once; a whole read at least every 7 days; a snapshot, a move or a restore drops the bitmap, and the next backup
  reads the disk whole.

- A Linux bridge can be made VLAN-aware from the dashboard (#345), as Proxmox VE's "VLAN aware" bridge: in a bridge
  network's details, **Make the bridge VLAN-aware** turns VLAN filtering on with VLANs 2 to 4094 tagged on its uplink,
  untagged traffic unchanged. The change is a trial the dashboard confirms from a new request a few seconds later; if
  the host can no longer be reached, it undoes the change by itself within 60 seconds. Kept, it is set again at every
  boot, before libvirt, by `hyperlite-vlan-bridges.service`, whatever manages the host's network. It can be turned off
  again while no VM keeps VLANs on the bridge (`POST`/`DELETE /networks/{name}/vlan-aware`).

- VLANs on VLAN-aware Linux bridges (#345), as Proxmox VE's `tag` and `trunks`: on a bridge network whose bridge
  filters VLANs, a VM interface takes an access VLAN (untagged inside the VM) and trunk VLANs it sees tagged
  (`10,20-30`). libvirt refuses a VLAN on a Linux bridge, so Hyperlite records it in the VM's definition and sets it on
  the VM's bridge port: when the VM starts or arrives by migration (a libvirt hook the package installs, which restarts
  libvirt once; running VMs keep running), when it is changed on a running VM, and when the service starts. A port
  whose VLAN cannot be set carries no traffic rather than the bridge's untagged one, and says why in the journal
  (`journalctl -t hyperlite-vlan`). The network list tells which networks take VLANs and how (`vlan_mode`).

- Copies of a backup store, as Proxmox Backup Server's sync jobs (#420, step 4, part 1): a store made as the copy of
  another, on a pool at the other site, receives on its own schedule the backups it lacks, only the chunks it lacks,
  copied as they are (encrypted ones never decrypted); the copy keeps a retention of its own, so a backup deleted at
  the source, by its retention, by mistake or by an attacker, stays in the copy. The recovery of a lost site now also
  reads a backup store (`/backup-store-syncs`; the dashboard comes next).

- Encrypted backup stores (#420, step 3; `docs/design/backup-store.md`): a store's chunks and the files of its backups
  (NVRAM, TPM state, configuration) are encrypted on the node with AES-256-GCM before they reach the pool, as Proxmox
  Backup Server's client encryption. The key is kept encrypted in the cluster's secrets and shown once, as a code, a QR
  code and a file; an optional master recovery key (made by Hyperlite and given once, or the administrator's own RSA
  public key) lets a backup be read again even when every node and secret is lost. An encrypted store is taken over
  with its key's code, and `python -m app.core.backup_store_recover` reads a backup with nothing but the store and one
  key (`docs/backup-store-recovery.md`).

- A backup of today's format can be copied into a backup store (#420, step 4, part 3): **Into a store…** in a VM's
  backups, or `POST /backups/{id}/import`. It is verified first, its disks are read whole (a replication point with its
  chain) and cut into the store's chunks, only what the store lacks written; the backup itself stays as it is.

- Copies of another cluster's backup stores (#420, step 4, part 2), as Proxmox Backup Server's remotes: the backup site
  pulls a store of the other site over its API, its HTTPS certificate pinned, with the API token of a dedicated account
  there that may only read that store (the new privilege `backup_store.read`, the role "Backup store reader"). The
  copy of an encrypted store keeps no key at the backup site until it is restored from: the administrator unlocks it
  then with the key's code (`/backup-remotes`, `copy_of: "<remote>:<store>"`, `POST /backup-stores/{name}/unlock`).

- Backup stores, as Proxmox Backup Server's datastores (#337, #420; `docs/design/backup-store.md`): a store lives in
  a directory of a storage pool (directory, NFS, SMB) and keeps every VM's disks as 4 MiB chunks named by their
  digest, each held once whatever the number of VMs and backups that share it; a backup is a complete image, never a
  chain. VMs with ZFS, LVM or iSCSI disks can be backed up into a store, running or stopped; a running VM's disks are
  read as they were when the backup began, while it keeps writing. Restores over the VM (file disks swapped in at
  once, block devices written in place) or to a new VM; verification, retention, deletion as for every backup, and a
  weekly garbage collection, one node at a time in a cluster. In the dashboard, **Backups › Backup stores** makes,
  cleans up and removes them, and a VM's schedule, **Back up now** and the grouped jobs choose their **Destination**;
  through the API, `/backup-stores` and `store` on backups, schedules and grouped jobs.

- A VM exported from VMware as an OVA can be imported (#426): upload the `.ova` with the imported disks, then **Create
  a VM** on its row (Library > Imported disks). Its settings are read from the OVF descriptor (vCPUs, memory, disks,
  BIOS, UEFI or UEFI with Secure Boot, the system's family, network cards), its disks copied out of the archive and
  checked against its manifest, and the VM created from them, stopped (`GET /vm-disks/{file}/ovf`,
  `POST /vm-disks/{file}/extract`, `disks[].import_disk` in `POST /vms`). A live connection to an ESXi host is not
  there yet.

- In a cluster, the CSV exports of the tasks and of the audit log hold every node's rows, as the lists do (#441): each
  member's export is read while it arrives and merged newest first, with a last column `member` (`?cluster=true` on
  `GET /tasks/export.csv` and `GET /audit/export.csv`, which the dashboard now asks for). The members that did not
  answer are named in the `Hyperlite-Unreachable` header and said by the dashboard; a member that stops midway ends
  the download with an error instead of a file missing its rows.

- Hook scripts on a VM's lifecycle, as Proxmox VE's hookscripts (#515): an administrator attaches a script that root
  placed in `/var/lib/hyperlite/snippets/` on the node (Hardware > Advanced > Hook script, `PUT /vms/{name}/hookscript`).
  It runs as root with the VM's name and the phase at pre-start, post-start, pre-stop and post-stop, for every start
  and stop (the dashboard, the start at boot, HA, `virsh`) through libvirt's qemu hook, pre-stop before a stop asked
  through Hyperlite. A failing pre-start refuses the start, a failing pre-stop refuses a shutdown (not a forced stop);
  the output is in the task's log and the VM's last runs.

- Lockdown of `hyperlite-admin` (#516), as ESXi's lockdown mode: an administrator can refuse root of a node its
  session as `root@<node>` (Administration › API, or `PUT /api/v1/admin-cli`), on every member and for the sessions already
  open; root must then use a named account's API token (`--token-file`), so every action is under a person's name.
  Off by default.

- `hyperlite-admin`, the administration CLI of a node (#516), as `qm`, `pct` and `pvesh` on Proxmox VE: any route of
  API v1 (`hyperlite-admin get /vms`), short commands for VMs, containers, storage, networks, backups, tasks, the
  cluster and the node (`hyperlite-admin vm migrate web1 node3`), the parameters of a route (`hyperlite-admin api`),
  `--json` for scripts and the tasks waited for with their log. As root it needs no token: the node signs it in as
  `root@<node>`, an administrator that has no password and no way in from the web, under which the audit log records
  its actions on every member. Deletions, restores and updates ask first, or take `--yes`.

### Changed

- The parity page counts the replicated configuration as the only source of truth as done (#416): every node applies
  the cluster's configuration in one order, read-only without quorum, and shows every node's tasks, audit log,
  backups and their CSV exports; the hosts driven over SSH that still need a controller are deprecated.

- The accounts, API tokens, groups, roles, resource pools, ACLs and SSO/LDAP settings move to a file of their own,
  `/etc/hyperlite/access.db`, readable and writable by root only, at the first start of this version (#511). A table of
  those names made in the main database is never read in its place. A return to an older version puts them back into
  the database.

- The files the web API reads and writes are now shared with the `www-data` group, as on Proxmox VE (#511): the
  database, the claim files, the TLS certificate's key, the upload directories and the access log. A
  `tmpfiles.d` entry, `/etc/tmpfiles.d/hyperlite.conf`, sets this up at every boot. The API itself still runs as root;
  this prepares it to run as `www-data`.

- The API's last local libvirt changes outside HA go through `hyperlite-daemon` (#511, step 4 preparation): the
  dashboard's default storage pool, made when missing and read again, and the guests' shutdown before the host
  reboots or powers off. Every route now reads libvirt through a read-only connection.

- The backup stores run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 2): a store made or
  taken over, an encrypted copy unlocked, a backup into a store, an import into one, a copy job, a garbage collection,
  what a store holds, and another cluster's stores. Only the daemon opens a store's key and a remote's token. A
  remote's token is now sealed with the address and the certificate it was given with, and is only ever sent there,
  whatever the records say later; a remote recorded before keeps the address of its record.

- The directory backups and the VM exports run in `hyperlite-daemon` instead of the API (#511, step 3, backups
  part 1a): a backup's copy, its restore over the VM or as a new VM, its verification and its deletion, an export's
  copy and its deletion. The daemon checks a backup's directory itself (under the default directory or an
  administrator's target, never a system directory) and deletes in it only a backup's own files: a backup directory
  that holds other files is kept, and its deletion refused with the names of those files. Cancelling a backup's task
  still stops its copy.

- The copy of the configuration to and from the cluster's replicated store (hyperlite-cfs) is done by `hyperlite-daemon`
  instead of the API (#511). This covers both loops, the confirmation of a change before its answer, and the actions of
  Administration > Replicated configuration. The daemon now loads the node's `/etc/hyperlite/priv/env`.

- The replicated configuration service (`hyperlite-cfs`) now accepts changes from root only (#511). Its socket is
  readable by the web API's group, `www-data`. The API's own few changes (a new row's id, the rolling update's lock, the
  certificate fingerprint, the node's lead) now go through `hyperlite-daemon`. Each refused change is written to
  hyperlite-cfs's log with the caller's uid.

- The copies and the commands between nodes are done by `hyperlite-daemon` instead of the API (#511, step 3, cluster
  domain part 4). This covers an ISO copied to or from another node, listed or deleted there; the configuration copy
  (the daemon builds the bundle with the node's keys and sends it); a node's boot id and host name; the fixed probes
  of another node's statistics and hardware; and the peer calls' fallback over SSH, which still carries the user's
  ticket. The API names a node and the work, never an address, a path or a script. Another node's summary, address
  use and metrics are read through the daemon's read-only tunnel. The answers are unchanged.

- The cluster's SSH keys are used by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain part 1).
  This covers testing a node before it is registered, the public key shown to authorize it, and recording its host
  key. It also covers giving and revoking the reverse trust in root's `authorized_keys`, and the node checks of the
  node poller. The daemon reads a registered node's address from the node registry itself. It checks an address
  before using the key. The answers are unchanged.

- A node registered over SSH only is changed by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain
  part 2). This covers its VMs' power, pre-stop script, start at boot, libvirt autostart flag, guest agents, rename,
  hook script and deletion (with Hyperlite's rule on the disks it keeps), its storage pools, LVM report, default pool
  and its host's details. The daemon opens that node's libvirt with the cluster's key, which only it reads. The API
  reads the node through a read-only tunnel the daemon opens to the node's read-only libvirt socket
  (`/run/hyperlite/nodes`). The answers are unchanged. Migration and HA still use the API's connection until they
  move.

- The consoles and the shells are opened by `hyperlite-daemon` instead of the API (#511, step 3, consoles domain):
  the host shell, a container's root shell and SSH terminal, and a VM's serial console, SSH terminal, VNC console and
  SPICE display. The daemon runs the session as root and hands its terminal or its socket to the API, which relays it
  to the browser. It reads the display's port, the guest's address and account itself, and reaches another node with
  the host key recorded when the node was registered. The host shell starts in a clean environment instead of the
  service's. When the SSH terminal cannot connect, ssh's own message is shown, then Hyperlite's.

- A container's backup archives run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 4): the
  archive of a stopped container's filesystem, its deletion and its restore. The daemon archives the filesystem the
  container's definition names, so a container kept in a storage pool's directory is archived too (it used to be
  looked for in the default directory only), and it deletes or unpacks an archive of the containers' backup
  directory only.

- A container is created and cloned by `hyperlite-daemon` instead of the API (#511, step 3, containers part 2). The
  daemon checks the request again: the name, the account, the network, the limits and the storage pool, read from the
  pool's own definition. It then pulls and unpacks the image, prepares the filesystem, reserves an application
  container's address and writes the definition, escaping the network and the directory. Unknown fields in a
  request are refused. A system container that does not start is no longer left defined without its filesystem.
  An application container whose process did not start said "Container defined purpose its process did not start":
  it now says "Container defined but its process did not start".

- Starting, stopping, renaming and deleting a container, and changing its settings, are done by `hyperlite-daemon`
  instead of the API (#511, step 3, containers part 1). The daemon finds a container's filesystem in its libvirt
  definition and only accepts it in a directory where Hyperlite puts containers. It no longer takes it from the API's
  records. A container whose definition names any other directory keeps that directory when it is deleted, and the
  audit log says so; its rename and DNS changes are refused. The answers are otherwise unchanged.

- A container's snapshots are listed, taken, restored and deleted by `hyperlite-daemon` instead of the API (#511,
  step 3, containers part 3). Its console log (what `docker logs` shows) is read through the daemon too. The daemon
  finds the container's directory in its definition, so a snapshot is only ever written beside a filesystem Hyperlite
  manages. Every check runs before the task starts, so a refusal is the answer. The answers are unchanged.

- `hyperlite-daemon` checks what each of its operations needs of the person who asks (#511, step 4d), as Proxmox VE's
  `pvedaemon` checks each method's permissions:
  - an administrator by default;
  - otherwise the privilege the route itself asks for, on the VM, container, storage pool, network or backup store
    it names;
  - or the account itself, for its own second factor, keys, tokens and password.

  A request whose route forgot a check is refused by the daemon (403). Calls of this node's own services keep working.

- The ISO library and the disks to import are written by `hyperlite-daemon` instead of the API (#511, step 3, disks
  domain part 1). It stores an upload, renames or deletes an ISO, deletes a disk to import and extracts an OVA's
  disks. An upload is written into a spool (`/var/lib/hyperlite/spool`), then moved into its library by the daemon,
  as Proxmox VE's pveproxy and pvedaemon do. The daemon checks the names again and refuses anything in the spool that
  is not a file of its own (a link to another file). It keeps an ISO that a VM has in its CD-ROM drive. The answers are
  unchanged. An interrupted upload left in the spool for more than a day is removed when the service starts.

- Growing a VM's disk and moving it to another storage pool are done by `hyperlite-daemon` instead of the API (#511,
  step 3, disks domain part 2). The daemon is asked for the VM by its name and the disk by its target, never a path.
  It checks the request again, drops the replication checkpoints only once the request is accepted, and writes a
  move's progress into its task. Cancelling the task stops the copy in the daemon. The answers are unchanged.

- The file restore runs in `hyperlite-daemon` instead of the API (#511, step 3, backups part 3): the daemon opens a
  backup's images with libguestfs, read-only, only in a backup's own directory, and hands a downloaded file to the
  API as an open file, removed as soon as it is sent.

- HA runs in two services of its own, as Proxmox VE's `pve-ha-lrm` and `pve-ha-crm` (#511, step 4c, #333):
  - `hyperlite-ha-lrm`, on every node: the watchdog, the monitoring of the node's HA VMs, the node's heartbeat and
    the load-balancing moves asked of it;
  - `hyperlite-ha-crm`, active on the leader: the recovery of a lost member's HA VMs and load balancing's evaluation.

  A hang or a crash of the API or of the cluster's decisions no longer stops the watchdog's feeding, so a healthy node
  is no longer reset because of them. A dead LRM still stops both the feeding and the heartbeat, so a member's VMs are
  never restarted elsewhere while they still run. The HA and load-balancing pages show the same state as before.
  Logs: `journalctl -u hyperlite-ha-lrm` and `-u hyperlite-ha-crm`.

- The scheduled work runs in a service of its own, `hyperlite-scheduler`, as Proxmox VE's `pvescheduler` (#511,
  step 4c): the scheduled backups (with the backup groups and the stores' copies and garbage collections), the
  replication, the automatic deletion of inactive VMs, the update check and the start at boot. It starts and restarts
  with the API, keeps running if the API crashes, and its tasks appear and can be cancelled in the dashboard as
  before. Its log: `journalctl -u hyperlite-scheduler`.

- The automation jobs' steps run in `hyperlite-daemon` instead of the API (#511, step 3): a command as root on the
  host, or over SSH with the automation key on a VM, whose address and account the daemon reads itself. Cancelling a
  run still stops the step in progress. A step's output is kept up to 1 MiB each, then marked as truncated.

- A live migration is done by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain part 3). The daemon
  opens both nodes' libvirt with the cluster's key, prepares the destination (its networks, its default pool, the
  VM's addresses, the disks and CD-ROM files) and runs the migration, writing its progress into the task. A
  cancellation aborts libvirt's job. The API then writes where the VM runs (HA protection first, tags and notes,
  start at boot) and asks the daemon to clean the source up. The daemon kept what to clean, so the API never names a
  file to delete. The answers are unchanged.

- The network firewall's iptables rules and the VLANs of Linux bridges are set by `hyperlite-daemon` instead of the API
  (#511, step 3, network domain part 1, `app/daemon/ops/network.py`): applying or removing a network's firewall, the
  trial, confirmation and undoing of a VLAN-aware bridge, the VLANs of a running VM's ports, and their replay when the
  service starts. The daemon is asked by a network's or a VM's name and reads the bridge and the ports itself, so the
  API cannot point them at another interface; the API keeps who may ask, the audit log and the firewall's rules.
  Without the daemon, these answer that it is unavailable (503).

- libvirt's networks are created, started, stopped, edited, renamed and deleted by `hyperlite-daemon` instead of the
  API (#511, step 3, network domain part 2a): the daemon writes a new network's definition itself from checked fields
  (`app/core/network_create.py`), never libvirt XML from the API, and checks again that a system network or one a VM
  still uses is not deleted. The routes and their answers are unchanged; without the daemon they answer that it is
  unavailable (503). A list of the networks no longer redefines the isolated network every time: only when it is
  missing or stopped.

- A network's DHCP reservations, added and removed from the network's page, are written by `hyperlite-daemon` (#511,
  step 3, network domain part 2b), which checks the MAC address, the name and the address again. The routes and their
  answers are unchanged.

- The permissions are written by `hyperlite-daemon` only (#511, step 4b), as `pvedaemon` is the only writer of
  Proxmox VE's `user.cfg` and `priv/`. This covers:
  - the accounts, the API tokens, the security keys, the second factors and the sign-outs;
  - the groups, the custom roles, the resource pools and the ACLs, including their renames and those of the VMs and
    containers they name.

  The API asks the daemon for each change and no longer writes these tables itself. A second factor's secret is made,
  sealed and checked by the daemon. In a cluster, these changes still answer once the cluster took them.
  `hyperlite.service` now starts after `hyperlite-daemon.service`, which tells systemd when its socket answers.

- The replication's points run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 1b): the daemon
  copies a VM into the other site's directory, full or incremental through libvirt's backup API, starts a stopped VM
  paused for the copy and prunes the old chains. It takes the chain's state from the API's records only as point and
  checkpoint names of its own form, never as paths. The site recovery reads the VMs through a read-only connection
  and restores through the daemon, as the backups do.

- The claims that keep one operation at a time on a VM or a container (a backup, a restore, a snapshot, a migration)
  are files of `/run/lock/hyperlite`, locked by the process that works on the guest, as Proxmox VE locks a guest with
  `/run/lock/qemu-server` (#511, step 4c). Every Hyperlite process sees them: once the scheduled backups and
  replication run in a service of their own, an operation the API is asked for is still refused while one of them
  works on that guest. A process that stops, even killed, leaves no claim behind. `HYPERLITE_LOCK_DIR` moves the
  directory for development and tests.

- Signing in is done entirely by `hyperlite-daemon` (#511). It records the last sign-in, and for single sign-on it builds
  the redirection to the identity provider, checks the provider's answer and creates or updates the account. The API
  only handles the browser's redirections and cookies.

- On an installed node, the API no longer runs what nobody asks for: hyperlite-daemon makes the admin account, checks the initial password and seals the second factors at its own start; hyperlite-scheduler polls the nodes, copies the configuration, and sets the network firewall's rules and the running VMs' VLANs again at its start, so a scheduler stopped by hand leaves them as they are until it starts again (it restarts with the API).

- The storage pools made, renamed and removed from the storage page on this node are handled by `hyperlite-daemon`
  instead of the API (#511, step 3, storage domain part 2a). This covers directory, NFS, SMB, iSCSI and LVM pools, with
  the SMB credentials file, the iSCSI CHAP secret and the LVM thin pool. It also covers the NFS and SMB permission
  check, and the `default` pool made at the first listing. The daemon writes a pool's definition itself from checked
  fields, checks them again, and refuses to remove a pool that holds files or that a VM uses. A pool on another node
  keeps that node's connection, with the same checks and answers. The answers are unchanged. A pool's refused creation,
  rename or removal is now always written into the audit log.

- The storage page's reads that need root on this node are done by `hyperlite-daemon` (#511, step 3, storage domain
  part 2c). That is a volume group's LVM report, which gives an LVM pool's thin pool and its thin volumes, and a pool's
  refresh before its volumes are listed. The storage domain of the storage routes is then done. The answers are
  unchanged.

- The volumes created and deleted from the storage page, on this node's libvirt pools, are handled by
  `hyperlite-daemon` instead of the API (#511, step 3, storage domain part 2b). The daemon writes a volume's definition
  itself from its checked name and size, and refuses to delete a volume a VM uses, whatever the API checked. The
  answers are unchanged.

- The ZFS pools and volumes made and removed from the storage page are handled by `hyperlite-daemon` instead of the API
  (#511, step 3, storage domain part 1): loading the kernel module, creating and deleting a pool, creating and deleting
  a volume. The daemon checks the names again and refuses to delete a volume a VM still uses. The routes and their
  answers are unchanged; the volume's deletion records "Confirmation missing" in English in the audit log.

- The work a request starts in the background (a backup, a migration, a clone, a disk move, an ISO copy, a host upgrade...) keeps that user's proof for hyperlite-daemon until it ends: a task ticket bound to its task, for any account, valid while the task runs, instead of a session that could expire or be revoked meanwhile.

- A VM is cloned, and a template made, deployed, renamed and deleted, by `hyperlite-daemon` instead of the API (#511,
  step 3, VM domain part 3c). The template files are root's. The daemon checks the source VM's state, the names, the
  room for the copy and a template's linked clones again, and writes the new definition itself. A Kubernetes cluster's
  VMs are started and stopped through it too. The answers are unchanged. An unexpected failure during a clone is no
  longer described in the answer; the daemon's log has it.

- A VM is created by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 3a). The daemon checks the
  specification again against its own libraries, pools and VMs, then makes the disks, the cloud-init drive, the DHCP
  reservation and the definition. It writes the definition from the checked fields, never from XML or paths sent by
  the API. The answers are unchanged.

- A VM's firewall, hook script, name and cloud-init drive are changed by `hyperlite-daemon` instead of the API (#511,
  step 3, VM domain part 2c). The daemon builds the firewall's filter from checked rules and checks a hook script again
  in the root library. It renames the files named after the VM with it, and writes the cloud-init drive private to
  root. The answers are unchanged. An interface the firewall could not be applied to said "Filter created purpose not
  applied": it now says "Filter created but not applied".

- A VM is deleted by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 3b), with Proxmox VE's rule
  for its disks rather than vSphere's. Its own disks and the drives Hyperlite made for it go. A disk another VM uses,
  a disk another VM's linked disk reads through, or a file outside every storage pool of the node is now kept: vSphere
  deletes a disk shared with another VM, and a file attached by hand is not Hyperlite's to delete. The answer lists
  what was kept and why (`kept_disks`), as do the audit log, the automatic clean-up's entry and the dashboard.

- A VM's devices are changed by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 2b): disks, network
  interfaces with their VLANs, PCI and USB passthrough, and CD-ROM drives. The daemon finds the volume, the network
  or the ISO itself, checks a disk image before a VM uses it, and writes each device's definition from checked fields.
  It keeps a VM's last interface. The answers are unchanged.

- Starting, shutting down, stopping, rebooting and resetting a VM of this node are done by `hyperlite-daemon` instead
  of the API (#511, step 3, VM domain part 1). This covers the dashboard's buttons, the repeated shutdown request,
  the start at boot and libvirt's autostart flag. The VM's pre-stop hook script is run as root by the daemon, and the
  guest agents' addresses in the VM list are read through it. The daemon checks the VM's state again. A VM on a node
  registered over SSH only keeps that node's connection. The answers are unchanged.

- A VM's processors, memory, resource limits, CPU pinning and advanced hardware (disk options, boot order, memory
  ballooning, machine type) are written by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 2a).
  The daemon checks the fields again and changes the VM's definition itself. The answers are unchanged.

- A VM's snapshots are taken, restored and deleted by `hyperlite-daemon` instead of the API (#511, step 3, VM domain
  part 4): libvirt's internal snapshots, ZFS snapshots of every zvol and LVM-thin snapshots alike. The daemon checks
  the name, the VM's state and the snapshot again, and builds a snapshot's definition itself. An LVM-thin VM's list
  is read through it, since `lvm` needs root. The answers are unchanged. An unexpected failure is no longer described
  in a task's error; the daemon's log has it.

### Deprecated

- Hosts driven over SSH from one node (one controller, nodes over SSH), the configuration copy and `hyperlite-promote`
  are deprecated (#416): they work as before for as long as API v1 lasts and go with v2. A node joins a cluster of
  equal nodes instead (the **Cluster** page), as on Proxmox VE; a site across a WAN will be one of several autonomous
  sites driven from one view (#656). Their routes (`POST /nodes`, `POST /nodes/test`, `GET /nodes/cluster-pubkey`,
  `GET` and `POST /nodes/config-copy`) are marked `deprecated` in the API's description and answer with a
  `Deprecation` header and a `Link` to `docs/cluster-failover.md`; the dashboard says so where such a host is added.

### Fixed

- Automatic HA recovery (#333) is safer:
  - a node whose Hyperlite service was stopped normally (an update, a restart) is never powered off, and its HA VMs
    are never started elsewhere while they still run there;
  - a target node that cannot be reached is passed over for the next one, instead of stopping the round;
  - a VM an earlier round started is found where it runs and never started a second time;
  - each VM restarted takes its memory out of its node's free memory, so one node is not overcommitted;
  - a BMC that did not confirm the power-off is asked again, up to three times, before the VMs go to manual;
  - a leader that loses the lead mid-round restarts nothing more.

- HA recovery (#333): a member back after it was fenced never gets a VM that runs there, or one stopped on purpose, started elsewhere later; a service stop never lets a round re-arm the watchdog; a libvirt error no longer turns a VM's protection off; a failed manual recovery leaves nothing that refuses the next one.

- Reading a VM (`GET /vms/{name}`) on the member a live migration is leaving answers 404 instead of a 500 when the VM is
  undefined there between its lookup and its read (#334).

- Two backups of a VM into a store within the same second (an unchanged VM's incremental takes less) no longer
  collide: the second waits for the next second instead of finding the first's directory or checkpoint already
  named (#420).

- A VM created from an imported disk with an explicit system family (`guest_os`) now gets that family's devices:
  an imported Windows guest had VirtIO disks and card, which Windows without VirtIO drivers does not boot from. An
  imported disk's name that leaves its directory is refused with a 422 instead of an error 500 (#426).

- The database health test suite passes on Debian 13 (#440): its SQLite (3.46) already sees, in the daily quick check,
  index entries that disagree with their table, which the test expected only the full check to see. Hyperlite then
  names the same index and offers the same online repair.

- A node whose Corosync runs an older configuration than the one written reloads it again, so a joining node is
  not left out of the ring by a reload that did not take.

- A failure of `hyperlite-daemon` answers 500 (`internal`) again, as these routes did before their work moved to the
  daemon, instead of 502 (`peer_error`), which API v1 keeps for another member of the cluster: the host's settings
  (DNS, host name, time, syslog, automatic updates), the update channel, the start of an update or a rollback, and the
  VLAN-aware trial of a network (#511). A call the daemon cannot answer is always a 503 (`unavailable`) with the reason,
  and a network removed while a request was being handled is a 404.

- A user with rights on a VM, not an administrator, is no longer refused by hyperlite-daemon where the dashboard lets them in: a backup of that VM into a store (its retention and its cancellation included), a VM created from another node's ISO, and the cluster's lists when a member is reached over SSH.

- The single sign-on and LDAP settings are written by `hyperlite-daemon`, as the other permissions since 4b (#511).
  These settings decide who may sign in and which of the identity provider's or directory's groups are
  administrators. The daemon also seals their client secret and bind password with the node's keys; the API did this
  until now.

- Each task records the process that runs it, so tasks are handled correctly once some run outside the API
  (#511, step 4c: `hyperlite-scheduler`, HA's services):
  - a restart of one service no longer closes as interrupted the tasks another one still runs;
  - cancelling such a task asks the process that runs it to stop, instead of closing its record while it goes on.

  The tests also reset libvirt's last error before each test, which made an HA test fail now and then.

- **Joining a cluster from a loaded or slow host no longer gives up after a minute** (#607): the joining node waited 60 seconds for the quorum, counted from the restart of its Corosync, and its links to the members came up only once they reloaded with it, which took longer on a loaded host; the join rolled back with the member still listing the node. It now waits up to five minutes, as Proxmox's `pvecm add` waits for the quorum, and says so in its log. Found by the cluster lab on GitHub's runners (twice).

- A node without libvirt's LXC driver lists no containers instead of answering 500, and a route on a named container
  answers 501, saying the node has no LXC driver (#631). A node installed from the package always has the driver.

- Messages read "purpose" where they meant "but" (#703), for example "Zvol created purpose device never appeared" or
  "Shadow mode is on, purpose the first copy did not complete": a leftover of the API v1 renames, which turned the
  French column `but` into `purpose` and reached the English word too. A few audit entries and one error left in
  French are English now ("missing confirmation", "Missing URL", "working tree not clean", "threshold N day(s)").

- A BMC password an agent echoes is hidden even where its output is cut; an SSO group claim sent as a string counts as one group; an unreachable identity provider is said without the connection error's details to the anonymous visitor; a snapshot name with ZFS's range or list syntax is refused.

- Retention never counts nor deletes a backup imported from another site's storage, which may be the only copy left of its data; a failure after a live disk move switched the VM to its new file keeps both files and says what to do, where the new file was deleted under the running VM.

## [1.3.3] - 2026-10-24

### Security

- The workstation tunnel connects only to an address the host binds to the VM's own network cards (its DHCP lease or the host's ARP entry for its MAC), never to one the guest agent alone reports (GHSA-4vfp-w7wf-jv75).

- A container's web terminal takes the administrator role, as a VM's: it signs in as an account with passwordless sudo, and a container's root has the host's capabilities. An operator of the container keeps its console log (GHSA-527j-fwg2-7646).

- Approving a `hyperlite login` takes typing the code printed in the terminal: the approval page no longer takes it from its link, where one click on a link someone else sent approved their workstation (GHSA-fvhx-jrg7-hgxm).

- A container's console log is opened without following links at any level: a link put there by the container's root can no longer have the service read or truncate a host file (GHSA-fvvw-286q-82jj).

- The `hyperlite` CLI opens or prints only a sign-in page of the server it signs in to, and refuses an http:// server address but for this very computer, so its token never travels in clear (GHSA-m44w-x95g-jp7m).

- Calls between cluster members check the peer's certificate fingerprint on every connection, a connection reopened after the peer closed it included, and a closed connection is no longer kept (GHSA-mv7v-9gqp-797f).

- A WebAuthn challenge, an SSO state and an SSO hand-off code are each taken by one statement: presented twice at the same instant, they are accepted once (GHSA-xp4w-9555-wj48).

## [1.3.2] - 2026-10-23

### Added

- LVM-thin storage (#417): an LVM pool can name a thin pool, an existing one of its volume group or one Hyperlite
  creates over its free space. VMs created there get thin disks, which take room only as they are written, and
  snapshots: disk only, taken in about a second per disk (a running VM with several disks is paused meanwhile),
  restored with the VM stopped, and kept after a restore, so a VM can go back to the same snapshot again. A deleted
  VM takes its thin disks and their snapshots with it. The pool list shows the thin pool's size and fill.

### Security

- An HS256 token signed with the key the members share is accepted only while a member is known to run a version before 1.3.0: never on an error reading the membership or a member's keys, and no longer as a session signed before the update, whose signing time is whatever the token says. Sessions signed before 1.3.0 that were still open must sign in again (GHSA-498v-p9g9-wvr8).

- A session alone no longer enrols a second factor on an SSO account (its identity provider's multi-factor authentication applies), a directory account removes its own with its directory password, and an administrator can remove an account's second factors (POST /auth/users/{name}/second-factor/reset) (GHSA-7wjv-ggfp-7x9c).

- hyperlite-daemon counts wrong passwords and second-factor codes itself, out of the API's reach, and refuses an account or a pre-authentication token that failed too often (GHSA-83f3-qj22-m659).

- LDAP groups are matched by their full DN only: a bare name also matched a group of that name anywhere in the directory. Groups saved as bare names match nothing any more and must be given again by their DN (Administration › LDAP); allowed groups saved that way still restrict sign-in. A directory user name is one account and one sign-in lock whatever its case (GHSA-8qgj-pvwg-xj37).

- A disk image that names another file to read with it (a backing file, an external data file, a VMDK extent) is refused when it is imported, attached, restored or browsed; an attached volume is declared with its real format, and a volume already used by a VM is refused (GHSA-q6pm-r5rj-538c).

- An update from git (a development checkout) is refused unless the service runs with HYPERLITE_GIT_UPDATES=1, and installs Python dependencies by their hashes and npm dependencies without their install scripts; a node that is neither a checkout nor the package is no longer taken for one (GHSA-rm3v-9jj9-7qmj).

- A new VM's disk is never written over a file already in the storage pool, such as another VM's disk: the creation refuses a name whose disk file exists (GHSA-vfmj-3v3j-48fq).

- A node's address and SSH user are checked before any connection to it, on every path that registers or reaches one, and ssh is told where its options end (GHSA-x3f5-r8vr-pvgw).

## [1.3.1] - 2026-10-23

### Added

- LVM storage pools (#417): a pool on an existing volume group, or on an empty disk Hyperlite builds one on. A VM
  created there gets each disk as a logical volume (a raw block device, the cloud image or imported disk written into
  it), grown with `lvextend` and deleted with the VM. The pool's volume list shows the thin volumes and thin pools
  libvirt does not see, and the pool is removed only once its group holds no volume at all: libvirt's own pool
  deletion, which wipes the whole group, is never used. VM snapshots and live migration are refused on LVM for now.

### Fixed

- **Updating a cluster node from 1.1 keeps its access assignments and starts at once** (#330): the start that rebuilds the `acl` table (the storage and network assignments of 1.2) copied its rows as new ones, and asked the cluster for their ids while it was still agreeing on its state after the update. The start failed, and the next one, finding the new table already made, left every assignment in the old one, unread. The rows are copied with their own ids, and a start that finds the old table finishes the copy. Found by updating a two-node production cluster from 1.1.3 to 1.3.0, whose table was empty.

- **Updating Hyperlite alone no longer kills apt-get after five minutes** (#330): the update without the Debian packages ran `apt-get install` with a 300-second limit, which killed it in the middle of dpkg on a loaded node while a new dependency was still unpacking; the packages stayed unpacked and not configured and the node on its old version. It now runs as the full upgrade does: to its end, in a unit of its own, its output in the task's log. Found by updating a two-node production cluster from 1.1.3 to 1.3.0.

## [1.3.0] - 2026-10-23

### Added

- **An ISO image uploaded to any member** (#330): `POST /isos?node=<member>` sends the upload to that member, as Proxmox VE relays an upload to the node that stores it. The file is read from this node's spool while it is sent, over the members' HTTPS (pinned certificates), never held in memory; where that way is closed the answer is a 502 that says so. The Terraform provider's `hyperlite_iso` takes `node`.

- **`?node=` on everything a node holds** (#330): every route under `/vms`, `/templates`, `/networks`, `/storage` and `/isos`, and a VM's backups, backup schedule and export, sends a request naming another member of the cluster to that member, which does the work itself with its own rights check, locks, task and audit entry, as Proxmox VE's pveproxy does. Only start, stop, `DELETE`, the disk and network reads and a few others took `node` before, reaching the other node through libvirt over SSH; creation, cloning, `PATCH`, disks, interfaces, cloud-init, snapshots, templates and networks acted on the node that answered only. A node registered over SSH only is still reached by the routes that took `node`; any other route refuses it (409) rather than act here on an object of the same name. `GET /tasks/{id}`, its log and its cancellation take `?node=` as well as `?on=`. Console and tunnel tickets stay where their WebSocket opens; an ISO upload goes to the member too. What the Terraform provider's `node` uses. `GET /health` names this node to a signed-in caller (`node`), and the Go library reads it (`Health.Node`).

- Container snapshots (#424): a stopped container's files can be snapshotted, restored and deleted
  (`GET/POST /containers/{name}/snapshots`, `POST …/{snapshot}/restore`, `DELETE …/{snapshot}`), from its new
  Snapshots tab. libvirt's LXC driver has no snapshot: each one is an rsync copy of the container's directory, next to
  it, keeping only the files that changed since the previous one (compared by content). A restore copies the snapshot
  and swaps it in, and leaves the container's files as they were if it fails. Snapshots follow a renamed container and
  go with a deleted one; one operation at a time runs on a container's files. The package depends on `rsync`.

- SPICE consoles (#518), as Proxmox VE's: a VM can get a SPICE display next to its VNC one (`POST /vms/{name}/spice-display`,
  effective at its next start), and the console's SPICE menu downloads a `.vv` file for virt-viewer
  (`POST /vms/{name}/spice-ticket`). virt-viewer goes through Hyperlite's SPICE proxy on port 3128, which admits only
  that ticket, for 30 seconds; SPICE is in TLS only, checked against the node's own CA, and listens on 127.0.0.1. The
  package sets each node up (`scripts/ensure-spice-tls.sh`: certificates, `spice_tls` and a default SPICE password in
  `qemu.conf`) and depends on `qemu-system-modules-spice`. Checked with `remote-viewer` on libvirt 10.0 and QEMU 8.2.

### Changed

- **The CI no longer downloads the same packages in every job** (#583). The runner's Ubuntu mirror made `apt-get install` take up to 22 minutes, and `playwright install --with-deps` up to 14, in jobs whose tests took 3: a full run took 20 to 50 minutes. The packages a job installs are kept as the `.deb` files of the runner image they were fetched on (`.github/actions/apt-install`), and Chromium per Playwright version; only pushes to test and master write them, a pull request only reads them. The workflows are checked by zizmor in the CI (template injection, permissions, pinned actions, caches), and the publication receives its four secrets by name instead of every secret of the repository. The night's VM lab and hyperlite-cfs soak (eleven runners) run on test, and only when cfs/ or the lab changed there that day: they tested master, unchanged between releases, every night. The public mirror is checked every six hours rather than every hour; each publication still checks it right away. The package job keeps what apt and pip download in its Debian containers (`installer/build-cache.sh`): each kept `.deb` is checked against the signed index before use, since apt itself installs a kept file without checking it.

### Fixed

- **The first node of a new cluster is reached over HTTPS at once** (#330): it published its certificate's fingerprint only at the next round, up to ten minutes after the cluster was created; meanwhile the other members called it over SSH, and an ISO upload could not be relayed to it.

- **A certificate fingerprint copied from a browser is accepted** (#330): `hyperlite-cli` and the Terraform provider pinned a fingerprint only in the dashboard's form (64 hexadecimal digits); the form browsers and `openssl` show, pairs separated by colons, was taken for another certificate and refused. Both forms pin the same certificate.

- **A node can join a cluster again** (#330): since 1.0 the API is under `/api/v1` and the old addresses answer 410, but a joining node still posted to the member's `/cluster/members`: every member refused it ("This address is gone since Hyperlite 1.0"), and no node could join a cluster from the dashboard or the API. It posts to `/api/v1/cluster/members` (the old address only to a member older than 1.0). Found by forming a cluster of three VMs from the package (`lab/tests`).

- **Adding the second node no longer leaves the cluster without quorum** (#330): the member letting a node in recorded it in the shared nodes table through the copy that is confirmed with the answer, after its Corosync had reloaded with the new member; the cluster, without the quorum until that node is up, refused the copy, the join failed, and the cluster stayed read-only waiting for a node that had given up. The row is now confirmed while the quorum holds, and a join that cannot be recorded leaves the cluster as it was. Found by forming a cluster of three VMs from the package (`lab/tests`).

- **A node that has just joined a cluster can be called by the others at once** (#330): until it published its public keys and certificate fingerprint, the members took it for a node before 1.2 and sent it the shared key, which it refused (401), so an ISO upload or a migration to it failed for up to ten minutes. The joining node now sends both with its join, and the member records them before its Corosync reloads, while the cluster still has the quorum. Found by forming a cluster of three VMs from the package (`lab/tests`).

- **A change to another member is never made twice** (#330): when its answer was lost over HTTPS (a timeout, a dropped connection), the change was sent again over SSH and could be made a second time (another snapshot, another VM). It is now sent once, and the 502 says it may have been made; a member that does not answer is reported with its reason.

- **A VM migrates to a node that never used its default storage** (#330): libvirt creates the copied disk on the destination through the pool of its directory, and a fresh node defines its `default` pool (`/var/lib/libvirt/images`) only when something first asks for it; the migration failed with "no storage pool with matching target path", and its check said the destination's space could not be read. Both now make the pool first, as creating a VM does. Found by migrating a VM in a cluster of three VMs from the package (`lab/tests`).

- **A node that joined a cluster is reached over HTTPS within seconds** (#330): it publishes its certificate fingerprint and its public keys as it restarts, while the cluster reloads without the quorum; the refused write was taken for done and tried again only ten minutes later, so a file could not be relayed to it meanwhile. A refused write is now tried again within 15 seconds. Found by forming a cluster of three VMs from the package (`lab/tests`).

- **A node's keys and certificate reach the other members even after a failed first try** (#330): on a fresh installation hyperlite-daemon can start before the API has made the database; the thread publishing the node's public keys then stopped for good on the error, and the members called that node with the older shared key. The same held for the certificate fingerprint. Both threads now log a failed round and try again at the next.

- **A change made as the quorum comes back is no longer refused** (#330): a node kept its "no quorum" answer for two seconds, so the change made right after the quorum returned (the Cluster page already showing it) was still refused; adding the third node failed this way just after the second had joined. A refusal is now asked again each time. Found by forming a cluster of three VMs from the package (`lab/tests`).

- The backup schedule of a VM on another member was read and removed on the node that answered, which keeps none for it (schedules are kept by the node that runs them): an empty answer, and a removal that removed nothing. They go to the VM's node, as setting it did.

- **The service stops cleanly again** (#518): stopping the SPICE proxy called a method the service's event loop (uvloop) does not have, so every stop or restart of `hyperlite` ended with "Application shutdown failed". The proxy now closes the sessions it keeps itself. Found by forming a cluster of three VMs from the package (`lab/tests`).

## [1.2.0] - 2026-10-22

### Added

- An account that is not an administrator can create VMs, as vSphere and Proxmox VE allow it, with one privilege per step: `vm.allocate` on the pool the VM goes in (`resource_pool` of `POST /api/v1/vms`; the VM joins that pool, so the pool's rights apply to it), `storage.allocate` on the storage pool of its disks, `network.assign` on its network; `vm.allocate` also allows deleting the VMs of that pool. Creating a volume to attach as a disk needs `storage.allocate` on its storage pool (it needed the administrator role). Roles are assigned on a storage pool or a network from **Users and access › Assignments**. What a Terraform pipeline's token needs, and nothing more (#330). Such an assignment waits until every node of a cluster is updated: an older node could not store it, and would stop taking the cluster's configuration.

- Each object a list of API v1 gives can be read alone, with the same fields: a storage pool, a volume, a pool, a group, an assignment, a custom role, an account, a grouped backup job, a VM's HA protection (additions to API v1, `docs/api.md`). A custom role's privileges and a pool's description change in place (`PATCH`): a role changed by deleting and creating it again lost all its assignments. What the Terraform provider reads after each change (#330).

- A Go library for API v1, `github.com/Hyperlite-hv/Hyperlite/cli/hyperlite`: the client `hyperlite-cli` is built on, and the base of the Terraform / OpenTofu provider (#330, `docs/design/terraform-provider.md`, step 1). It holds the requests, the TLS rule (the system's trust store, then a pinned certificate), the errors of v1 with their `code`, the check of `/health` (API version, Hyperlite version) and the wait for a task, which fails with the task's error and the end of its log, and asks a task that outlives the wait to stop. `hyperlite-cli` behaves as before.

- `PUT /api/v1/vms/{name}/network` takes `?mac=`, the interface to move to another network: any interface moves in place and keeps its MAC address (a DHCP reservation and the guest's own configuration know it by it); only the first one could, the others had to be removed and added again. What the Terraform provider needs to change one of a VM's interfaces (#330).

- `GET /templates` gives each template's firmware (`bios`, `uefi`, `uefi_secure`) and its disk (`disks[].size_gb`, the virtual size rounded up: the smallest disk a VM deployed from it can have), as vSphere's template is read to size a deployment.

- A VM made from the cloud image is customised as vSphere's and Proxmox VE's guests are: its host name and domain, static addresses by interface (the others keep DHCP), resolvers and search domains, and a cloud-config of the user's (packages, commands, files) added to Hyperlite's own, on its cloud-init drive. At creation (`ssh_keys` and `customize` in `POST /api/v1/vms`, written before the first boot) or later (`PUT /api/v1/vms/{name}/cloud-init`, at the next boot); `GET` reads them back. What the Terraform provider's `customize` block writes (#330). Checked inside a real guest by the e2e suite: a VM created with a static address answers there once booted.

- `?wait=false` on VM creation, clone, template deployment and template creation (an addition to API v1): every check is made first, then the answer is `202` with the task's id at once, as the other long operations answer, instead of a request held for the whole disk copy, which a proxy cuts long before a large disk is copied. The Terraform provider follows the task (#330, `docs/design/terraform-provider.md`, section 6). Checked on a real libvirt by the e2e suite (`api-wait-false.spec.ts`).

- **SMB/CIFS storage pools**, as on Proxmox VE: the share of a Windows server or a NAS holds VM disks (#418). libvirt mounts it (a netfs pool of format cifs), so it is shared storage everywhere an NFS pool is: on every node or the ones chosen, with live migration and HA. The user and password are kept in a file only root can read (0600), on each node that mounts the share, as Proxmox VE does; the pool's definition only names the file, and the API never returns the password. A pool declared for every node also keeps it encrypted, for the nodes registered later. Every file of the share belongs to QEMU's user, so VMs open their disks. The SMB version is negotiated by default or fixed. A credentials file is the only way libvirt's mount takes a password: libvirt always adds `guest`, and mount.cifs then ignores a `password=` option. The package now depends on `cifs-utils`. `type: "smb"` and its `smb_*` fields are additions to API v1; a listed netfs pool now says its `protocol` (`nfs` or `smb`). The pool list also shows an NFS pool's version, which the dashboard used to drop.

- Linked clones (#423): a template can be deployed as a linked clone, whose disk is a qcow2 image backed by the
  template's disk; it is created at once and stores only its own changes (`linked` on
  `POST /templates/{name}/deploy`, off by default). A template is not deleted or renamed while a VM reads its disk,
  the template list names each template's linked clones (`linked_clones`) and whether it can have some (`linkable`),
  and a linked clone made into a template gets a disk of its own. Hot backups, disk moves, disk growth and internal
  snapshots of a linked clone were checked on libvirt 10.0 and QEMU 8.2.

- **Serial console** in a VM's Console tab and console window, next to the graphical console and the SSH terminal, as Proxmox VE's xterm.js console (#425). It shows what the guest prints on its serial port and the login prompt its system offers there, which helps when the guest's network or display is broken. It runs `virsh console` on the VM's own node, relayed to the browser, and takes the same privilege as the graphical console. A VM without a serial port gets one, effective at its next start. Every VM Hyperlite creates already has one. Linux shows its console there when booted with `console=ttyS0`, as cloud images are. `POST /api/v1/vms/{name}/serial-ticket` and the `/vms/{name}/serial` WebSocket are additions to API v1.

### Changed

- **Accounts that are not administrators** need the same rights on an existing VM, as Proxmox VE: attaching or growing a disk needs `storage.allocate` on its storage pool, attaching an interface or changing its network `network.assign` on that network, and cloning `storage.allocate` on the `default` storage pool (where a clone is copied). An account that did this with `vm.hardware`, `vm.resize` or `vm.clone` alone is now refused (403, naming the missing right) until it is given these rights; administrators are not concerned.

- The command-line client and its Go library (`cli/`) are under the Mozilla Public License 2.0 (`cli/LICENSE`), as the Terraform provider built on them will be; the rest of Hyperlite keeps the PolyForm Noncommercial License.

- A pull request adds its changelog entry as a file of its own, `changelog.d/<issue>-<slug>.<type>.md`, instead of editing `CHANGELOG.md`: two pull requests no longer conflict over the changelog, nor every open one with each release (#566). The release pull request runs `python3 scripts/changelog.py release <version>`, which writes the version's section from the fragments, in Keep a Changelog's order, and removes them; `scripts/changelog.py unreleased` gives the test channel's notes and the versioned release's. Entries still written under `[Unreleased]` are kept. See `changelog.d/README.md`.

### Fixed

- A VM made on another storage pool than `default` (a directory or NFS pool) had "no cloud-init drive": its Cloud-Init tab could not change its keys or password, and the Terraform provider could not customise it. Its drive is written next to its disk, and everything that finds it again read only the default pool's directory. The same mistake made a clone of such a VM boot with the original's host name and SSH host keys (no new cloud-init drive), and left its drive behind when it was converted to a template. A VM's own drives are now found by one rule, in the default directory or next to its disks, and never an image of the ISO library that carries the same name: deleting a VM could remove such an image.

- On a new node, creating a volume in the `default` storage pool, attaching a disk from it or creating a container there failed with "Storage pool 'default' not found" until someone had opened the storage page, the only place that made the pool: it is now made when it is first used (`lookup_pool`), and when a VM is created on it. Found by the Terraform provider's acceptance tests on a fresh node (#330).

- Three error messages had two words glued together (the HA protection refused on local storage, a container image without a package manager, an update refused on a modified tree).

- Renaming a network failed with a server error on a node whose libvirt has no LXC driver (a VM-only host): such a node simply has no containers. The notes and tags of a container on it answer "not found" instead of a server error.

- A storage pool whose only file left was the base image Hyperlite copies there for the VMs built on it could not be removed ("still contains 1 file"): it is Hyperlite's own copy, removed with a pool of this node and left on a shared (NFS, SMB) pool, where the other nodes' VMs may be built on it. Deleting a VM also refreshes the pools that held its files: they kept listing its disk and its cloud-init drive.

- A task that succeeded no longer carries the details of its audit entry as its `error` (a clone answered `"error": "clone -> copy"`): `error` is a failure's reason only, which a client of the API tests.

- Deploying a template and making a template from a VM now show in Tasks while their disk is copied; they had no task at all. Two creations or two deployments of a VM under the same name at the same time could write the same disk file: the second is now refused (409), as two clones already were.

### Security

- The SMB credentials files, written and removed as root, are checked by `app/core/smb_storage.py` itself with the path rule every other root-written file follows (`safe_child`): only a `.cred` file right in a credentials directory, never through `..` or a symbolic link. The API already validated the pool's name; the module no longer relies on its callers for it (#418).

## [1.1.4] - 2026-10-22

### Added

- A damaged database is detected and said, no longer only written to the log at every round of a background loop (#440). Each node checks its SQLite file at start and once a day (`PRAGMA quick_check`), and fully (`PRAGMA integrity_check`, every index against its table) when the metrics, the node poller, the backup scheduler or the configuration copy fail 3 times in a row on a damaged file. A damaged database shows a banner above every page, with the repair that fits, and sends the new **Database check** notification. Index entries that disagree with their tables are repaired from the banner, with the service running (`REINDEX`, `POST /api/v1/host/database/repair`). Damaged index pages, which SQLite cannot free, need the file rebuilt from its tables with the service stopped: `python -m app.core.db_health rebuild`, which keeps the damaged file next to the new one and can empty a damaged history table (`--drop-history`). `GET /api/v1/host/database` and `POST /api/v1/host/database/check` are additions to API v1. See `docs/database-health.md`.

### Fixed

- **A backup asked while another runs shows up.** One backup runs at a time on a node: one asked meanwhile waits for its turn before its row exists, and a VM's Backups tab read the list once, 1.5 s after the click, so it never appeared until the page was reloaded. The tab now follows it until it shows and says it waits for its turn; a container's Backups tab follows its backup's task and a running backup until it ends (it read the list once too). This was also the end-to-end test that failed first in 14 of the last 25 CI runs (#569).
- `.github/scripts/claim.py` sends its JSON bodies with `Content-Type: application/json`: a proxy that requires it refused `take` and `release`.

### Changed

- **The CI runs what a pull request needs** (#569): the end-to-end suite only for `app/` and the dashboard, hyperlite-cfs only for `cfs/` and its client, the workstation client only for `cli/`, the package for the code and the installer (`scripts/ci-scope.sh`); pushes to test and master still run everything. The end-to-end suite runs in four even parts (`next-audit.spec.ts` in parallel), hyperlite-cfs's clusters on runners of their own and its two fuzzers side by side. The required `e2e` check now also covers cfs, its clusters, the package and the client: a red one blocked nothing. CodeQL runs only when Python or JavaScript changes, and every Monday.


### Security

- This node's HTTPS certificate is handled by `hyperlite-daemon` (#511, step 3, host domain part 3): importing a pair, going back to the previous one, making a self-signed one, getting one from Let's Encrypt with certbot, and restarting the API afterwards (`app/daemon/ops/tls.py`). The private key is written to `/etc/hyperlite/local` by the daemon; the API checks who asks and answers as before.

## [1.1.3] - 2026-10-22

### Security

- Hyperlite's own update is done by `hyperlite-daemon` (#511, step 3, host domain part 2): it checks the request again (an update from the mirror or from a received file, the return to the previous version) and starts the job in a unit of its own, which outlives the restarts of the API and of the daemon that the package being installed causes (`app/daemon/ops/hyperlite.py`). The check for a new version fetches the package lists through the daemon, and the update channel is written by it. The API only asks; without the daemon, the update answers that it is unavailable.

### Security

- The node's own settings are done by `hyperlite-daemon`, as root, instead of the API (#511, step 3, first domain): the Debian package updates and their nightly schedule, DNS, host name, time and NTP, remote syslog, and the scheduling of a reboot or shutdown (`app/daemon/ops/host.py`). The API keeps who may ask, the audit log and the tasks; the daemon checks every argument again (`app/core/privileged.py`). Without the daemon these settings answer that it is unavailable.

### Fixed

- On a node whose DNS is managed by systemd-resolved, the DNS form showed the local stub (`127.0.0.53`, search domain `.`) instead of the servers it forwards to, and refused to save it back. It now shows systemd-resolved's own servers, ignores the `.`, and refuses a loopback address, which would make the node's resolver ask itself.

## [1.1.2] - 2026-10-22

### Fixed

- A node that is down no longer holds the pages that list the cluster: as Proxmox VE and vSphere, Hyperlite never waits for a host that is gone (`app/core/node_health.py`). A node the cluster sees down (the node poller found it offline, and it sends no status) is named at once and never asked: the ISO list, the VM list, a node's storage, the metrics. A connection to a node switched off seconds ago, not seen down yet, gives up after 8 seconds instead of the system's two minutes, libvirt's included (`scripts/node-ssh`). A node that does not answer is said unreachable (502) where a node's storage list failed with an internal error. The VM creation wizard shows this server's ISO images at once and adds the other nodes' when they come: with a node switched off the list stayed empty about 16 seconds. As in Proxmox VE, a node that is offline can no longer be chosen as the new VM's node.

### Security

- The calls between the members of a cluster no longer carry a session signed with the key the members share. Each member's `hyperlite-daemon` signs them with its own key, for one member and 5 minutes, and only for an account whose session, API token or rolling-update task it checked itself; the member called verifies them with the public keys each member publishes in the cluster's configuration (`/cluster/authkey/<node>`), as it already pins their certificates (#511, `docs/design/privilege-separation.md`, section 12.1, PR C). During a rolling update from an older version the shared key is still used with the members that published no keys yet, and accepted only while one remains; the sessions opened before the update stay valid 7 days at most.

## [1.1.1] - 2026-10-21

### Added

- **Create a template (copy)** in a VM's Actions menu, first of the two: the template gets a copy of the VM's disk and the VM stays, as vSphere's "Clone to Template" (`POST /api/v1/templates/from-vm/{name}` with `keep_vm: true`, an addition to API v1). The copy is refused before it starts when it would not fit, and a template being copied never shows in the list. "Convert to template" stays (the VM becomes the template, as on Proxmox VE), its warning now naming the VM that leaves the list: an administrator converted a VM expecting a copy.

### Fixed

- A VM deployed from a template takes the SSH user and the system of the VM the template was made from: an ISO-installed VM deployed from its template had none, and the web terminal could not reach it. Converting a VM to a template no longer leaves its SSH user and system recorded under the name of a VM that is gone.

- Converting a VM to a template forgets it as a deletion does: its backup schedule (which then failed at every run), its rights, pools, automatic deletion, start-at-boot setting, tags and DHCP reservation stayed under the name of a VM that is gone. Both now go through one list (`app/core/vm_records.py`).

### Security

- Sessions are signed by `hyperlite-daemon` with an Ed25519 key pair only root reads (`/etc/hyperlite/priv/authkey.key`), renewed every day; the API verifies them with the public keys (`/etc/hyperlite/authkeys.json`), as Proxmox VE's `pveproxy` does with `authkey.pub`. The first part of moving the sign-in out of the API (#511, `docs/design/privilege-separation.md`, section 12). Sessions opened before the update stay valid; the calls between the members of a cluster still use the key they share. Without the daemon, signing in answers that it is unavailable.

- `hyperlite-daemon` opens a session only after checking the proof itself: the password (local account or LDAP directory), the TOTP code, the security key, the one-time code an SSO sign-in leaves, and for a password change the session, the current password and the code (`app/core/sign_in.py`). The API keeps the locks after repeated failures and the audit log; it can no longer have a session signed for claims of its choosing (#511, section 12, PR B).

## [1.1.0] - 2026-10-21

### Changed

- Hyperlite's files live where Proxmox VE keeps its own (#511, `app/core/paths.py`, `docs/design/privilege-separation.md`, section 9): the code in `/usr/lib/hyperlite`, the web interface in `/usr/share/hyperlite/www`, the node's secrets in `/etc/hyperlite/priv` (no more `.env` next to the code), its certificate in `/etc/hyperlite/local`, the data in `/var/lib/hyperlite` (`template/iso`, `template/vm`, `dump`, `import`, the database). The first start moves the files of `/root/hyperlite` with the service stopped, all or nothing; a failure is rolled back by the 1.0.2 watchdog. The VMs with an ISO image inserted and certbot's renewal hook follow the move; the backup schedules and catalogue written before are read at the new place. What 1.0 leaves of `/root/hyperlite` is removed. The package refuses to install over a version older than 1.0.2, and the return to the previous version never goes below it.

- The database is readable by root only, as are the update archives, which now hold the code and `/etc/hyperlite` instead of `/root/hyperlite`.

- The return to the previous version (Node › Updates) installs it again through the update watchdog, with the service stopped, instead of from the running service: the previous version may move the files back while it runs (seen on a test node, from the new layout: a second, empty database).

### Fixed

- Removing the package stops and disables its services: they kept running on deleted code, and the next installation found them running. A purge also removes the systemd units it installed.

## [1.0.2] - 2026-10-21

### Added

- `hyperlite-daemon`, the root side of the API, behind a local socket (`/run/hyperlite/daemon.sock`, 0660): a fixed list of operations with checked arguments, the caller checked by its peer credentials, every call logged. The first step of the privilege separation (#511, `docs/design/privilege-separation.md`); the API does not use it yet.

### Changed

- The update is ready for the files to move where Proxmox VE keeps its own, in 1.1 (`/var/lib/hyperlite`, `/etc/hyperlite`): its watchdog runs from a copy kept out of the package and is told the version to wait for, a failed update is rolled back by reinstalling the package replaced, and the files of a newer version are moved back when it is rolled back to this one. An ISO library on another node is found in either place. Every node must run this version before 1.1.

### Security

- An image name given to the container creation form could make a regular expression backtrack for seconds (`app/core/image_env.py`, CodeQL py/polynomial-redos); the test is now linear.

- The cluster ISO list told the browser each unreachable node's raw SSH error; it now gives a fixed sentence, the detail in the service log. An ISO name is checked where it becomes a path, on this node and over SSH.

## [1.0.1] - 2026-10-20

### Fixed

- A dashboard tab left open over an update that moves the API reloads itself, instead of staying blank (at most once a minute).

- Hyperlite's own information messages reach the service journal: that a paused job resumed (scheduled backups, replication, start at boot, automatic deletion), or that the replicated configuration was rewritten with the names of API v1, never showed.

- An ISO name is checked where it becomes a path, in the library and over SSH on the other nodes, not only by the callers.

- **Going back to a version before 1.0 is no longer offered**: the update dialog proposed it after the update to 1.0, but 1.0 renamed the database and the cluster's configuration (API v1), which a dated version cannot read, and the return reinstalls the package only. The update to 1.0 keeps no dated package. The dialog's buttons wrap instead of overflowing its left edge.

- During the update of a cluster to 1.0, the dashboard said the node had lost the quorum: it now says the cluster waits for the nodes still on an earlier version, and names them (the API adds `read_only_cause` and `read_only_waiting_for` next to `read_only`).

## [1.0.0] - 2026-10-20

### Added

- The rolling update of a cluster knows the update to Hyperlite 1.0: its API and replicated configuration change names, so every node is updated in place, its VMs left where they run (no maintenance, no migration to or from a node already on 1.0, which an older version cannot talk to), this node last, each followed through `/health`. A cluster must be on this version before it is updated to 1.0.

- How long the users' addresses are kept, set by an administrator for the whole cluster (**Activity › Audit log › Retention**), for the GDPR: the access log, now written by Hyperlite to `/var/log/hyperlite/access.log` (one file per day, root and the `adm` group, 30 days by default, 0 for none) instead of the service journal, whose retention Hyperlite does not decide; and the audit log (365 days by default). The access log leaves out query strings.

- A primary node in a cluster (**Cluster** page, **Make primary**): it counts twice in the quorum, so with one other node it keeps working when that node is off or cut off, and that node alone becomes read-only. For a main machine paired with one that is often off or reached over a slower link, without a third machine as an arbiter. Proxmox allows it only by editing `corosync.conf`.

- In a cluster, a node that runs VMs with HA turned on fences itself when it is cut off from the majority, as on Proxmox: it arms its watchdog (a hardware one, or the kernel's softdog) and feeds it while it has the quorum, and without it the node is reset, so its VMs can be restarted elsewhere safely (the next step of automatic HA). A node without HA VMs never arms it, and a normal restart of the service (an update) disarms it first.

- In a cluster, a VM hosted by another node is backed up, restored, verified and scheduled from whichever node the dashboard is open on: the request goes to the node that hosts it, which keeps its backups. The Backups page lists every node's backups, with their node. Browsing a backup's files stays on the dashboard of the node that holds it. A node registered over SSH only keeps no backups: a backup of one of its VMs is refused instead of being taken from a VM of the same name on this node.
- In a cluster, the Activity page (tasks and audit log) and the recent tasks of the overview, of a node and of a VM show what every node did, from whichever node the dashboard is open on, as Proxmox does: each node keeps its own history, and the node that answers asks the others. A task held by another node opens, shows its log and can be stopped from any node. A node that does not answer is named above the list.

- One update per node: Hyperlite and the node's Debian packages together, from the account menu. The dialog (French and English) shows the versions, the Debian packages with the security ones counted, the release notes of every version in between, and whether the node will need a reboot, before anything starts; running VMs are not stopped, and a Hyperlite version that does not start is put back automatically. (#393)
- Going back to the version before the last update of Hyperlite, from the same dialog: its package is kept on the node (so it also works offline) and installed again under the same safety net; one step back, Debian packages unchanged. (#395)
- Debian's security updates every night (03:30 by default, changeable per node on Node › Updates, or turned off): security packages only, never Hyperlite, never an automatic reboot; a pending reboot is notified once per boot. Turned on with this update on nodes that never chose. (#394)
- Updating a node without network: every version publishes a signed update file next to the ISO (`hyperlite-update-amd64.tar`: Hyperlite, the Debian packages it needs, the release notes). **Check for updates → From a file…** sends it to the node, which checks the signature and every file, shows what will change, then runs the same update from the file's packages only, under the same backup and safety net.
- Rolling update of a cluster (Administration › Replicated configuration › Cluster update): a plan read from every node first (versions, Debian packages, reboots, the VMs that move and those that cannot), then every node updated one after another without stopping the VMs: maintenance, live migration away, update, reboot if needed, out of maintenance; the node that starts it goes last. A failure stops the run on its node, which stays in maintenance.
- An update channel per node (Node › Updates): `stable`, the releases, or `test`, every change merged for the next release as soon as it is merged (published as `1.0.1~test.<date>`, moving to the final release by itself). Going back to stable never installs an older version.
- A notification when a VM stops by accident (its QEMU process died, or the guest panicked), once per crash. Notification event names are shown in the dashboard's language. (#381)
- From 1.0.0, versions follow semver (`1.x.y`, the package carrying the epoch `1:`), and each release's notes (this file, and their French version) are published with it. (#384)
- Offline installation ISO: every package Hyperlite needs is on the ISO, no network needed. An automatic mode (root password and one confirmation naming the disk) and a guided one (admin password, optional email channel), each in French or English, on LVM ext4, with a manual network setup when DHCP does not answer. (#353)
- The sidebar groups pages by use (Resources, Infrastructure, Cluster, Protection, Supervision, Administration), and Tasks and the Audit log are two tabs of one Activity entry. (#357)

- Replication to another site: on the Backups page, a job copies the chosen VMs (all, a tag or a pool) to the other site's storage every few minutes (15 by default). Each copy holds only the blocks changed since the previous one (libvirt checkpoints and QEMU dirty bitmaps); a full copy starts a new chain each day and the two newest chains are kept. Stopped VMs are copied again only when they ran, started paused for the copy. Each VM's last copy is shown, with a warning when it is late. The other site restores the newest copy with Recovery of another site. Disk moves and resizes, snapshot reverts, offline snapshots, renames and migrations drop the replication checkpoint first (the next copy is then a full one); hot backups of a replicated VM keep it. Disks must be qcow2 files. See `docs/site-recovery.md`.
- Recovery of another site: on the Backups page, an administrator points at the storage where the other site's backups land, sees each VM's latest backup with the host that made it, and restores the chosen VMs here as new VMs, one after another, each after an integrity check, with a network to use when the other site's networks do not exist here. Nothing is overwritten. Backup manifests now record the host that made them. See `docs/site-recovery.md`.
- Renaming what could not be renamed yet: storage pools (stopped a moment and defined again with the same path, so the VMs' disks stay valid; a shared pool on every node that has it), networks (the stopped VMs and containers on it follow, its firewall and DHCP reservations are kept), VM pools, user groups, custom roles, automation jobs, API tokens, templates and ISO images. Still not renamable, with the reason shown: the `default` pool and the system networks, ZFS pools, Kubernetes clusters (their VMs are found by name), snapshots (libvirt cannot) and user names (the audit log and the sign-in sources refer to them).
- Shared storage on several nodes, as on Proxmox: an NFS or iSCSI pool can be created on every node (the default when there are several, including nodes registered later) or on the nodes chosen, under the same name and mount point; the form reports each node's outcome, the list marks shared pools, and removing one removes it from every node. The CHAP password kept for later nodes is encrypted.
- Administration › API: an **Open Swagger** button opens the API's Swagger (`/docs`) in a new tab, where **Authorize** signs in with a user name and password or an API token, and every call runs with that account's rights. `/docs` and `/openapi.json` are never public: the button gives the accounts an administrator chose (nobody, administrators by default, or every signed-in user) access for a few hours, withdrawn at once when the setting changes. Swagger UI is served by Hyperlite itself (no CDN).
- Renaming this node from its Actions menu: it sets the machine's host name (`/etc/hostname`, applied at once) and its `/etc/hosts` line, so the name shown is exactly the one given (`pve1` or `pve1.home`).
- Renaming a stopped VM (without snapshots), a stopped container and a registered node, from their Actions and right-click menus. Their settings, backups and schedule, pools and permissions, HA protection, start at boot, notes and tags and metrics history follow the new name; a VM's own cloud-init or installation drive and its UEFI variables are renamed with it, a container's directory and host name too.
- A root shell inside a running container, Docker or LXC, like `docker exec -it … sh` (administrators): bash when the image has it, else sh, with the image's PATH.
- The creation form asks for the variables some images need to start (PostgreSQL, MySQL, MariaDB, SQL Server, Oracle XE…), with a generated password to note down, and suggests their useful ones; the API refuses such a container without them instead of letting it stop at once.

- A user guide (`docs/user-guide.md`) that walks through every feature of the web interface: VMs, containers, storage, networks, backups and file restore, nodes, HA, users and sign-in, preferences and troubleshooting.

- Bulk actions on the VM list: select VMs (or every VM shown) and start, stop, force stop, restart, migrate or delete them together; the confirmation names them and the ones left as they are, and each failure is reported by name.
- Start at boot for VMs, with an order and a pause before the next VM, in a VM's Options. Run once per boot of each node (never on a service restart), on every node, and following a live-migrated VM.
- Notes (plain text) and tags on VMs, containers and nodes; tags show in the VM list, which filters by tag and finds them in its search.
- A log per task, shown in the task list, and cancelling a running task: migrations and node drains abort the libvirt job, disk moves, backups and exports stop their copy and remove partial files, automation runs stop the command in progress. A task nobody runs any more (the service restarted) can be closed, and the ones left over are closed at start-up.
- A page per container: resources, network interfaces and DNS servers, start at boot, notes and tags, terminal or process output, backups and history.
- A Tasks tab on VMs and containers with the object's own history.
- Cloud-init after creation: change a cloud image VM's SSH keys and password from its Hardware tab, applied at its next boot; its SSH host keys and Hyperlite's automation key are kept, the password is never stored.
- Advanced VM hardware settings under the Hardware tab: disk cache, discard, I/O mode, I/O thread and IOPS/MB/s limits (limits apply live), boot order across disks and network cards, memory ballooning with a minimum, and updating the machine type to the current version of its family.
- A Permissions tab on VMs and containers (administrators): who has rights on the object, its own assignments added and removed in place, and those a VM inherits from its pools.
- Edit a NAT or isolated network after its creation (subnet, DHCP range, NAT or isolated), with the subnet checked against the other networks; a DHCP range change is live, the rest is applied by restarting the network from its details. IP address management: DHCP reservations added, removed or made from a current lease, and lease end times.
- Pending changes under a running VM's header on every tab: the settings its next start applies (vCPU, memory, machine type, CPU, firmware, disks, network cards, boot order, passthrough devices), current and next values side by side.
- The HTTPS certificate on the local node's System page: its names, issuer and expiry; import a PEM certificate and key (checked before it replaces the current one, which is kept), get one from Let's Encrypt through certbot (renewed by certbot's timer, installed by `scripts/acme-deploy-hook.sh`), or go back to the previous or a self-signed certificate.
- An Updates tab on the local node: upgradable packages with the security ones marked, a reboot-required notice, and the upgrade run as a task with apt's output in its log (Hyperlite's own package is left to its update page). DNS servers, time zone and NTP, and remote syslog forwarding on the node's System page.
- Storage replication between nodes: decided not to build it for now, with the reasons, what covers the need today and what would reopen it (docs/design/replication.md).
- Reboot or shut down the local node from its Actions menu: its name typed back to confirm, running VMs and containers named, and shut down cleanly first when asked (the node stays up if one of them does not stop).
- My preferences (account menu), kept in the browser: the terminals' font and size, and the storage pools the Home page follows.
- A Metrics page (administration): the Prometheus endpoint with a ready scrape job, and metric servers the collector pushes every sample to (InfluxDB 2 over HTTP, Graphite over TCP), each with a test button and its last error.
- Backup jobs on the Backups page: one schedule for all of this node's VMs, those of a tag or of a pool (resolved at each run, some left out if wanted), run now or on schedule. GFS retention (last, daily, weekly, monthly) for these jobs and for each VM's schedule.
- VM list views: grouped by node, tag or pool (a VM under each of its tags or pools), optional columns (node, IP, system, CPU and memory, uptime), and saved views that bring back filters, grouping, columns and sort, kept in the browser.
- Help next to the technical settings (disk cache, discard, I/O, limits, ballooning, network mask, DHCP reservations, retention, metric servers, DNS search, syslog protocol, certificate chain): an (i) on the field, read on hover or keyboard focus, and a folded explanation for the disk options.
- VM console: type text into the VM as key presses (passwords at a login prompt, no agent needed), a Keys menu for the combinations the workstation would catch (Ctrl+Alt+F1/F2/F7, Alt+Tab, Alt+F4, Windows key, Print Screen), and the guest's clipboard when it sends one. USB redirection stays with RDP and USB passthrough (docs/workstation-access.md).
- File-level restore: browse a backup's filesystems from the VM's Backups tab and download a file, or a folder as a .tar.gz, without restoring the VM. The disks are read-only through libguestfs (never mounted on the host); needs `libguestfs-tools` and `python3-guestfs` on the node.
- Sign in with LDAP or Active Directory accounts (authentication page): the usual sign-in form, the user found by a service account and checked by binding as them (an empty password is refused), the role given by the directory groups at each sign-in, optional allowed groups, ldaps:// or StartTLS with certificate checking. Local accounts are never taken over by the directory.
- New `vm.options` and `container.options` privileges, part of the Manager role.
- Security response headers on every answer (Content-Security-Policy, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy); HSTS is opt-in with `HYPERLITE_HSTS_MAX_AGE`.
- Edit a notification channel in place (`PATCH /notifications/channels/{id}`).
- CSV exports of the full audit log and task history (`GET /audit/export.csv`, `GET /tasks/export.csv`), with every matching entry rather than the page on screen.
- Create and delete storage volumes from the Storage page; an "Imported disks" tab in the Library.
- Audit log retention (`HYPERLITE_AUDIT_RETENTION_DAYS`).
- Trusted reverse proxies (`HYPERLITE_TRUSTED_PROXIES`): the client address is taken from `X-Forwarded-For` only when the request comes from one of them.
- Sign out revokes the session server-side (`POST /auth/logout`).

- Move a VM disk to another directory or NFS pool from the Hardware tab (`POST /vms/{name}/disks/{target_dev}/move`, admin): live with a block copy and a pivot, or stopped with `qemu-img convert`; the original file is kept unless asked. ZFS and iSCSI disks, and VMs with snapshots, are refused with a clear message.
- Hyperlite Tools (the QEMU guest agent): shutdown and reboot through the agent with an ACPI fallback, the VM's IP address from the guest, quiesced hot-backup snapshots, the agent installed by cloud-init in new cloud-image VMs, and its state in the VM summary (`agent_invite`: `actif`, `inactif`, `non_configure`).
- Node maintenance mode (`POST`/`DELETE /nodes/{name}/maintenance`, `GET /nodes/{name}/drain-plan`, `GET /nodes/maintenance`): the node's running VMs are live-migrated to a chosen node one after another, the VMs that stay are listed with the reason, and the node receives no new VM and is never a migration or HA recovery target. From the node's Actions and right-click menus.
- Grow a VM disk from the Hardware tab (`POST /vms/{name}/disks/{target_dev}/resize`, privilege `vm.resize`): live or stopped for qcow2/raw files, `volsize` for ZFS zvols. Shrinking and iSCSI LUNs (sized on the storage server) are refused with a clear message.
- Right-click menus on every list with actions (VMs, nodes, containers, storage pools, networks, ISO images, templates, snapshots, backups, exports, users, high availability), with the same entries and rules as the Actions menus and row buttons.
- iSCSI storage pools: a target on a NAS or storage array (portal, IQN, optional CHAP kept in a private libvirt secret); VMs take whole LUNs, which are overwritten only after an explicit confirmation and never deleted with the VM.
- Create a VM from an ISO image stored on another node, and share ISO images between nodes from the Library.
- Change your own password from the account menu, and administrators can reset another account's password (its sessions and API tokens are revoked). Passwords follow a policy (12 characters at least, no account name, no common password or sequence) checked in the form as you type.
- VM list grouped under one band per node (load, running count, collapsible).
- Storage support check (`GET /storage/support`): the pool form says when the NFS client, the ZFS module (Secure Boot) or the iSCSI initiator is missing, and how to fix it.

- Workstation access: the `hyperlite` client (Windows, Linux, macOS; `cli/`) signs in through the web interface and opens SSH (`hyperlite ssh`) or remote desktop (`hyperlite rdp`) to a VM through a tunnel over the server's HTTPS port. New `vm.tunnel` privilege, allowed ports and limits in the environment, tunnels in the audit log, `hyperlite://` links from the VM console. See `docs/workstation-access.md`.
- API tokens can expire (`expires_at`); workstation tokens always do.
- A `Publish` GitHub workflow and `scripts/ci-publish.sh` that build, sign and publish the package, the APT repository and the ISO from a clean checkout (manual dispatch with a dry run for now); the signing passphrase and key location come from the environment.
- APT mirror monitoring: `scripts/verify-apt-mirror.sh` (run after each publication and every 30 minutes by a systemd timer, optional webhook alert), and an actionable message in the update dialog when the mirror is momentarily out of sync.
- Coordination between people and their AI assistants through a pinned GitHub issue, with rules in `CLAUDE.md` and `docs/onboarding.md`.
- Contributor onboarding guide (`docs/onboarding.md`): GitHub role and token, private network access, accounts, development environment and rules for AI assistants.
- E2E coverage for automation jobs, snapshot restore, SMTP notifications and multi-session behavior; optional Firefox and WebKit projects; advisory `e2e` CI job.
- Branching model documented in `CONTRIBUTING.md` (`test` = development, `master` = production); CI and CodeQL now also run on pushes to `test`.
- End-to-end web UI test suite (Playwright) against a real backend, with a coverage matrix in `docs/webui-test-matrix.md` (`npm run test:e2e`).
- Backups now record the VM's vCPU, memory and network so a restore to a new VM rebuilds the original hardware.

### Changed

- **API v1**, a change for the scripts that call the API: every route under `/api/v1` (the old addresses answer `410 Gone`, code `api_gone`, naming the new one); every name in English, keys, values and the database (418 names, the database migrated in place); every list answers `{items, total}` and every error `{detail, code}`, its code from a fixed list documented in `docs/api.md`; `/health` says `"api": 1`. Its contract, `docs/api/openapi-v1.json`, only ever grows: the CI refuses any change that is not an addition. A cluster updated to 1.0 is read-only while a member still runs an earlier version, then its replicated configuration is rewritten once with the new names. The dashboard and the `hyperlite` client use it; an older client says it must be updated.

- The package repository keeps the 8 newest stable releases and the 3 newest test builds instead of every version ever published: it had reached 2.1 GB, twice what GitHub Pages serves. A downgrade stays possible to any of those 8 releases. A version is published only once the CI has passed on its very commit.

- Faster with many VMs: one reading of a node's VMs serves the VM list and the network list for the requests of the same moment, answers are gzip-compressed (the VM list of 1,000 VMs went from 261 to 27 kB), and the dashboard loads only the language shown. Measured on a node with 1,000 VMs: network list 0.17 s → 0.009 s, VM list p95 with five tabs open 0.37 s → 0.27 s. (#385, #389)
- A VM (or a Kubernetes node) gets at most as many vCPUs as its host has CPU threads, as on Proxmox; the VMs together may still exceed them. (#368)
- `corosync` and `unattended-upgrades` are package dependencies; Debian's sample corosync configuration (unencrypted) stays stopped until a cluster is created. (#380, #394)
- Deleting a container asks for `?confirm=true`, as VMs, pools and backups do; an unknown API path answers a JSON 404 instead of the dashboard page. (#376, #375)
- The Activity button and panel left the top bar; node pages show the node's own tasks. (#355, #356)

- The VM list is about nine times faster on a large host: 1,000 VMs are listed in 0.4 s instead of 3.6 s. Each VM's XML is read once instead of three times, the states come in one call, the DHCP leases once per network and the SSH users and OS labels in one query each; on a remote node this also removes most of the SSH round trips.
- The Tasks page is laid out as a task list with status tabs (All, Running, Failed, with their counts) and, under it, the log of the selected task in a terminal-style panel, coloured by level. Columns: task, target, node, status with a progress bar, start, duration.
- The sidebar no longer shows the host name and node count box under the logo; the search stays in the top bar and on Ctrl+K.
- The interactive API documentation (`/docs`, `/redoc`, `/openapi.json`) is off by default; `HYPERLITE_API_DOCS=1` turns it back on.
- `/health` answers anonymous callers with the status only; the version is given to the loopback (update watchdog) and the full report to signed-in users.
- A TOTP code is accepted only once; enabling two-factor authentication or a security key asks for the account password.
- Session tokens carry an identifier and can be revoked; new API tokens expire after 90 days unless another lifetime is chosen, and a token can revoke only itself.
- SSO sign-in binds the identity provider's answer to the browser that started it (cookie) and hands the session over with a one-time code instead of a token in the URL; it asks for the second factor when the account has one.
- TOTP secrets are stored encrypted (existing ones are encrypted at start-up).
- Successful reads (`list_*`, `get_*`) are no longer written to the audit log, which the dashboard's polling was flooding.
- A VM is identified by its node and name in the dashboard; pages that act on the local host only are hidden for a VM on another node.
- Operations on one VM (backup, restore, snapshot, migration, disk move or resize, clone, export, template, deletion) are serialized: a second one gets HTTP 409 naming the operation in progress.
- Update backups are readable by root only and hold a consistent copy of the database (SQLite backup API) instead of the live file.
- The dashboard no longer loads fonts from Google (they were already bundled); design tokens give readable contrast for borders and secondary text and a visible focus ring.

- Hyperlite no longer caps a VM's vCPU, memory or disks from the host's size, like Proxmox and vSphere: the deployment profiles (homelab, standard, advanced), the allocation policies and their settings (`GET`/`PUT /host/profile`, `PUT /host/allocation`, `HYPERLITE_PROFILE`, `HYPERLITE_ALLOCATION`) are removed. Only technical floors and typo ceilings remain, plus the optional `HYPERLITE_VM_MAX_*` caps an administrator sets on purpose. The creation form still warns, without blocking, when a value exceeds the hardware.
- Accounts whose password predates the policy must choose a new one at the next sign-in before doing anything else.
- The package depends on `nfs-common`, so NFS pools work on an APT install.
- The dashboard scales with large screens (2K, 4K) instead of staying a small island of text.

- The historical interface is removed: the rebuilt dashboard is the only one (the `?ui=` switch is gone). The separate VM console, host shell and container terminal windows use it too and connect by themselves; its end-to-end specs were ported to the rebuilt screens.
- Publication is automatic: the `Publish` workflow runs on every push to `master` (package, signed APT repository, mirror, ISO release), replacing the post-merge hook on the build machine. The version is stamped into the artifacts only, so no version-bump commit or `master`/`test` synchronization is needed any more.

- The code repository moved to the `Hyperlite-hv` organization; links, package homepage and the ISO address follow, and the transitional legacy mirror is retired.

- The public APT repository moves to the organization mirror (`Hyperlite-hv/hyperlite-hv.github.io`, independent of the code repository); previous mirrors keep receiving publications during the migration.

- The ISO download address joins the APT address in `installer/apt-source.conf`; a test checks that the README links to it.

- The APT repository address now lives in one file (`installer/apt-source.conf`) read by the ISO build, the post-install script, the mirror check and the publishing hook; a test fails if another address is hard-coded.

- All repository content, user-facing text, logs and error messages are now in English. API and database wire identifiers and values stayed French until API v1 renamed them (above).
- The web dashboard requires Node.js 20.19+ to build (React Router 7, Vite 7).
- The legacy vanilla-JS frontend (`/legacy`, `/static`) was removed; the React dashboard is the only interface.
- The APT repository location used by the appliance installer is configurable (`HYPERLITE_APT_URL`) instead of being hard-coded.

### Changed (installer)

- The installation ISO and the offline update file no longer carry Python wheels of their own: the hyperlite package carries its environment, so they are lighter and an offline installation runs no pip at all.
- An installation or an update builds and downloads nothing on the node: hyperlite-cfs and the Python environment are built once, in the Debian 13 the package is for, and shipped in the package with the wheels they are made of. Nodes no longer need a compiler or development packages (gcc, meson and the -dev libraries are no longer dependencies; on a node updated from an older version they stay until `apt autoremove`), and an update no longer depends on PyPI being reachable. If the shipped environment does not work on a node, it is rebuilt from the wheels without network; if that fails too, the previous one stays and the update is stopped.
- Every Python package Hyperlite installs, its own dependencies and pip included, is pinned with its hashes (`requirements.txt`, generated from `requirements.in`): a node installs exactly what was tested, a file whose hash differs is refused, and pip is no longer upgraded to the newest version of the day. The package is for Debian 13 only, the one system of 1.0: Debian 12 and Ubuntu are no longer tested or published.
- The appliance ISO embeds the Hyperlite package and repository key and installs from them, so an installation no longer fails when the APT repository is unreachable or transiently inconsistent.
- The appliance installer no longer sets a default root password: it asks for one, as the Proxmox installer does. The Hyperlite `admin` account gets the random initial password generated by the package. The package no longer changes the Linux root password.
- The local host is always labelled `local`; rows still using a legacy label are migrated at start-up.

### Removed

- Unattended installation from an ISO (Kickstart for the Red Hat family, autoinstall for Ubuntu): a VM created from an ISO is now always installed by hand through the console, as on Proxmox VE, whatever the system, and its account is created in the installer. The Debian cloud image (with its account, password and SSH keys) and the templates stay the prepared paths. The API loses `GET /api/v1/vms/{name}/provisioning`, removed before 1.0 so that API v1 never carries it. A VM created earlier keeps working; its answers ISO (`<name>-oemdrv.iso`, `<name>-autoinstall.iso`) is still deleted with it.

### Fixed

- Replication: the first copy of a VM that could not be made (an old qcow2 disk, a raw disk) failed on a database error that hid why; it now says why, and the job's page shows it.

- In a cluster, a node back after a network cut is shown as on line and listed again within about 10 seconds: it stayed offline until the next check, up to a minute, and the Activity views skipped it meanwhile. A node still sending its status is asked whatever the last check said.

- In a cluster, a configuration change answers once the cluster took it, as on Proxmox: the node first takes in what the other nodes changed just before, and when another node changed the same thing first the answer is an error (409, "changed on another node meanwhile", nothing applied) instead of a success undone a second later. A change the cluster refuses (no quorum) or does not confirm within 15 seconds answers 503. When the cluster overrides a change made in the background (a task, a scheduler), a notification is sent (new event, on for channels that receive every event). Error answers made by Hyperlite itself (no quorum, these) now carry the security headers too.

- In a cluster, the VM list of the dashboard no longer asks the other nodes: every node sends a summary of its VMs to every node every 10 seconds, and at once after a change made through Hyperlite, and the list is read from it, as Proxmox does. A change made outside Hyperlite (a guest shutting itself down) shows within 10 seconds. Measured on the lab with 1,001 VMs over 3 nodes: the list from 600 ms (p95) to under 50 ms, and 20 users at once from 2 s to 0.3 s, without errors. A node whose summary is late is shown as such, one that stopped sending as not answering.

- In a cluster, the dashboard's VM list and the views of every node are much faster and no longer fail under load: another member's VMs come from that member in one call (they were read one by one through libvirt over SSH), and members call each other over HTTPS on kept connections, each one's certificate pinned through the cluster's configuration, instead of a new SSH connection per call, which sshd refused past 10 at once. Measured on the lab with 1,001 VMs: the views of every node from 230 ms to 30 ms, the VM list from 270 ms to 70 ms, and 20 users at once without errors. A node that does not answer is named instead of failing the request.

- Creating a VM on a NAT network of a large cluster no longer waits for every node in turn to say which addresses it uses: they are asked at once (100 simulated nodes: from 20 s to under 1 s).

- In a cluster, a change a node made to the shared configuration while it started (before its copy to the cluster ran) was dropped as "changed in the cluster meanwhile", which nobody had done: a node leaving maintenance at the end of a rolling update, or the admin account flagged for a password change, could keep the cluster's older value. Such a change now goes out, and still loses to a change the cluster really made meanwhile.

- A clone that would not fit in the images directory (on the system disk by default) is refused before the first byte is copied, with what is free and what is needed.

- A restore that would not fit is refused before anything is written, with what is free and what is needed: a restore over a VM writes the backup's images next to its disks before swapping them, so it needs room for both, and a full system disk also stops Hyperlite's database, the journal and libvirt. A restore refused because the VM's disks no longer match the backup no longer resets the VM's replication, which then had to copy the whole VM again.

- Two VMs created at the same time on one network could get the same first free address: libvirt refused the second reservation and that VM was created without its fixed address, with nothing said. Addresses are now handed out one at a time, moving on to the next one when an address was taken meanwhile. A network whose IPv6 subnet is listed before its IPv4 one no longer fails every creation on it. A new VM's MAC address is also checked against the node's stopped VMs and the networks' reservations, not only the active leases.

- The HA watchdog now reads back the timeout its device really uses: some drivers refuse the 60 s Hyperlite asks for and keep their own, and the node still announced that it would be reset within 125 s, the time the cluster will wait before restarting its VMs elsewhere. A device that does not report its timeout is armed but never vouches for a reset time (its VMs are recovered by hand), and one whose timeout is too short to be fed safely is not armed; both are written in the node's journal.

- **Security:** the admin account's random initial password was written in clear in `/etc/motd` and `/etc/issue`, which every local account and service of the node can read, and in the installation log (`/var/log/apt/term.log`); it also stayed in its files after being changed. It is now shown only on the physical console's banner (a root-only file), it must be changed at the first sign-in, and changing it removes every copy. In a cluster, the other nodes remove their own copy within 10 minutes of the change. A node installed before: a password already changed is removed from the disk when the service starts; one still in use is changed at the next sign-in.

- A disk resize that was refused (a smaller size, a busy disk) no longer resets the VM's replication, which then had to copy the whole VM again; and when the replication's bookkeeping cannot be removed from the disk, the resize fails with that reason instead of an internal error.

- A live migration that succeeded could be shown as failed, and then leave the VM's HA protection, tags and start at boot on the node it had left, when a step after the move failed (cleaning its old disks or addresses on the source node, for instance). HA could later restart a second copy of the VM on that node. The VM's HA protection now moves first, each step after the move runs whatever happened to the one before, and a step that fails is a warning on the finished task.

- Snapshot tasks are listed with their VM: they named the snapshot, so a VM's Activity tab never showed them and the audit log did not say which VM a snapshot was taken, restored or deleted on. A snapshot task that failed on anything other than a libvirt error (removing the replication bitmaps of a stopped VM, for instance) stayed running forever with no audit entry; it now fails with the reason.

- Cloning a VM whose disk is a raw file (a VM defined before Hyperlite managed the node) gave a clone that did not boot: the copy is a qcow2 file and the clone still read it as raw. A copy that stopped (a full disk, for instance) left its partial file in the images directory, and an unexpected error left the copied disks and a task shown as running; both are now removed and the task fails with the reason. Two clones to the same new name at once are refused.

- **Security:** a VM's password was written in clear on its cloud-init drive, a file that stays attached to the VM, readable by every local account of the node (and from inside the VM, through its CD-ROM drive), and copied with the VM's pool. It is now written as a SHA-512 hash, as Proxmox does, at creation and when changed from the Cloud-Init tab, and the drives are private to root. Drives made before keep their content until the VM's password is changed: change it from the Cloud-Init tab to remove it.

- **Backups:** a backup that had just succeeded was marked failed and deleted when the retention that follows it hit an error; it now stays, and the error is reported. Corrupted backups no longer take the place of good ones in the retention, so newer corrupted copies can no longer push out the last good one. A backup that would not fit on its target (the system disk by default) is refused before anything is written, with 1 GiB kept free, instead of filling the disk.

- **Data:** a VM could be started while a restore was replacing its disks (or while a cold backup, a disk move or a clone copied them): it then wrote to files the restore deleted at the end, and a cold backup was no longer consistent. Starting a VM, its forced restart and the start at boot now wait for such an operation to end ("the VM is busy"), as Proxmox's lock does.

- On a server in a language other than English, the migration compatibility check failed instead of warning when the destination did not know the source's CPU model: libvirt writes its errors in the server's language, and the check looked for the English message. The destination's known models are now asked instead. Hyperlite also runs its tools in a fixed locale (C.UTF-8), so reading their output no longer depends on the server's language.

- **Security:** the API tokens of a deleted account stayed in the database and were found by their owner's name, so they worked again for any account later created under the same name (a former employee's script signing in as a new colleague, with the new colleague's rights), and so could a session of the former account opened shortly before. Deleting an account now deletes its tokens, a new account starts without any left by a former one, and every session is bound to the account itself, not only to its name.

- In a cluster, when Corosync stopped on a node (an upgrade, a crash), that node's `hyperlite-cfs` kept running without it: it spun on a processor core and answered no request, so the node stayed read-only until the daemon was restarted by hand. It now exits, and systemd restarts it until Corosync is back, however long that takes.

- "Go back to the previous version" could offer a much older version than the one just replaced: when the replaced version's package could not be kept (a node installed from the offline ISO, updated from a file, or without network), the package kept by an earlier update stayed. The return is now simply not offered then, as the update's log says.

- In a cluster, every node refreshed the HA definition of every protected VM each minute, so the nodes kept overwriting each other (a conflict a minute in the audit log), and any node that did not find a VM could turn its protection off for the whole cluster. Each node now refreshes the protected VMs it hosts, and only writes a definition that changed. Two nodes writing the very same value no longer count as a conflict.

- In a cluster, the HA alert about a node that went down (its protected VMs) could be lost when that node was the one leading the cluster: the node taking over had often seen it go down before it led, and only the leader of that moment reports. It now reports it as soon as it leads, if the node is still down.

- In a cluster, a node cut off from the others (no quorum) could still change the configuration, and once back its changes overwrote the ones the majority made meanwhile, without a word. As on Proxmox, such a node now changes nothing until the quorum is back, and its dashboard says so above every page: the dashboard and the API refuse every change there (VM start and stop included; reading and signing in still work), scheduled backups, replication and the automatic deletion of VMs wait, and VMs set to start at boot start once the node has the quorum. A change still on its way when a node is cut off is dropped if the others changed the same thing meanwhile: the majority's version is kept, and the conflict is in the audit log.

- Deleting a Kubernetes cluster right after it showed as ready could be refused as "being created": it was shown ready a moment before its creation let go of it. Both now happen at once.

- In a cluster, signing out now signs the session out on every node. A session token is valid on any node, but the sign-out was only known to the node that received it, so a copy of the token (another tab, a stolen token) kept working on the other nodes until it expired.

- Publishing a version stopped before the ISO and the update file for nodes without network were released: the update file was written to a path relative to a temporary directory. The APT repository was already up to date.

- A backup can no longer be deleted while it is being read: during a restore, a verification or a file-restore session, deleting it is refused and says what holds it, and a schedule's retention keeps it until its next run. Deleting it during a restore made the restore fail half way, and during the copy could have left a VM half restored.

- The Account security window (two-factor authentication and API tokens, from the user menu) was in English whatever the language chosen; it is now in French too, as are the disk and ISO upload areas, whose sizes and speeds also mixed French and English units. A network error during a disk or ISO upload no longer breaks the upload area.

- In a cluster, VMs on the per-node NAT networks no longer get the same address: an address is handed out only when it is free on the network of the same name of every node, and a migrated VM keeps its address (its reservation goes to the destination and leaves the source). A migration is refused, before anything changes, when another machine holds that address on the destination (#410).

- Live migration between two other nodes (a drain or a rolling update driven from a third one) lost the VM's cloud-init drive: it was fetched to the driving node instead of the destination, which refused the VM. It is now carried from the source to the destination.
- Live migration from another node to this one required a Tailscale address and a dedicated "reverse" SSH key, which a cluster made by joining never has: it now uses this node's cluster address (or its route to the source) and, in a cluster, the source's own cluster key.
- Rolling update: its plan judged every VM as unable to move, the other nodes were not reachable (the command sent over SSH was split by the remote shell), a node already up to date was emptied and updated for nothing, and VMs were not sent to a node already on the new version when one was.
- A shutdown request the guest misses (just after a live migration, or while it boots) is sent again after 30 s and 90 s while the VM still runs: the VM used to keep running with nothing said. Asked with a forced stop after a delay, it is still stopped by force at the end.
- Live migration between cluster nodes failed for every VM created by Hyperlite: the CPU check compared the running VM's live CPU (refused even on its own host), libvirt refused VMs with snapshots (they now go along on shared storage), and a VM on a shared pool was based on an image only its first node had (now in the pool itself). Migrations take 6 to 10 s on a lab cluster. (#391)
- A guest whose agent hung froze the VM list for everyone (13 minutes measured on a lab node) and its stop never answered; the list now waits at most 0.5 s for the agents, and agent operations give up after 10 s and fall back to ACPI. (#385)
- A VM restored under a new name, or deployed from a template, never got an IP address (its network configuration was bound to the original's MAC): it gets a new cloud-init drive, as a clone does, and keeps its OS label and SSH user. (#362, #370, #377)
- On a node installed from the package (Debian 13), libvirt's default network was left stopped and the first VM could not start; starting a VM on a stopped network names it. (#386)
- Two VMs created at once on a new node could start from a half-downloaded base image, which a failed download left for every later VM. (#388)
- A network on an address range already in use was half created with a raw error; uploading an ISO under an existing name replaced it silently (even one in use) and an interrupted upload left a truncated file; starting a running container answered a 500. (#358, #369, #366)
- Backup schedules stayed after their VM was deleted, and one could be created for a VM that does not exist. (#363, #361)
- The audit log no longer keeps the successful reads logged before they stopped being audited (over 100,000 entries on one host). (#379)
- Wording, plurals, CPU topology on non-English hosts, empty CD drives, and a confirmation before removing an administrator's rights. (#378, #367)
- The cluster configuration service refuses a change at the end of its id or version counter instead of overflowing (found by fuzzing). (#360, #396)

- The pool creation form: required fields are named and each one says what is wrong instead of a greyed-out button; examples read as examples ("e.g. …"); an NFS share needs its server and exported path, both absolute and checked; CHAP takes a user and a password together (the API no longer drops a password given alone); ZFS can no longer be picked for a remote node, which the API refuses.
- Stopping a Docker container did nothing (PostgreSQL kept running): its process now receives SIGTERM, as with `docker stop`, and a container still running after 30 s (90 s for LXC) is stopped by force.
- A Docker container whose command is a link to an absolute path inside the image (`sh` on Alpine, a link to `/bin/busybox`) was refused with "not found in the image".
- The host shell started without job control ("no job control in this shell").

- Terminals (node shell, VM SSH console, container terminal and their windows) fit what they draw: the size sent to the shell counted the box's padding and border (the last row and a column were cut), the VM console's terminal kept a fixed height inside its 16:10 box (three rows cut), a separate window did not shrink with the window, and the size now follows any change of the box (zoom, display scaling, sidebar) and is measured once the terminal font is loaded. The node shell uses the height of the window instead of a fixed 26rem.
- Narrow and short windows (laptops at 125 or 150 % display scaling): the path in the top bar stays on one line, and the navigation is denser on short screens.
- A dashboard file that fails to download (a network change or drop, or files replaced by an update since the page was opened) no longer leaves a blank page: the page reloads, at most three times a minute.
- Pages no longer ask the server again at each render: the translation function changed at every render, so the effects that load data kept running (the Network page called GET /networks hundreds of times a second, each call written to the audit log).
- A backup schedule's directory is now checked: an absolute path outside the system's own directories (it was written as root wherever it pointed).
- The NFS permission check writes its probe file only inside a pool mount point Hyperlite created (under `/var/lib/libvirt/hyperlite-pools`); a pool mounted elsewhere is reported as not checkable.
- An automation job run is no longer reported as started, nor audited as a success, before it exists; invalid steps are refused with HTTP 422 and a crashed run is closed as failed.
- Disks written by Hyperlite itself (clone, restore, move, resize, templates, ISOs) are found without depending on libvirt's volume cache.
- Name validation matches the whole name, names the resource in the error, and is applied to clone, template and ISO names that escaped it.
- Restoring a backup over an existing VM is verified first and atomic (temporary files, rename, rollback); an overlay left by a failed hot backup is merged back instead of being lost.
- Automatic cleanup never deletes a VM without the warning it promised, and one failing VM no longer stops the others.
- An update rollback leaves Git on the previous commit and restores the database copy.
- The console and terminal of a VM on another node connect through that node.
- Account lockout counts per account and address, so an attacker can no longer lock an administrator out from anywhere; re-authentication has its own counter.
- The `hyperlite` client sends `HYPERLITE_TOKEN` only to `HYPERLITE_SERVER`, refuses server addresses with shell characters, and `logout` says when the token could not be revoked.
- The datacenter compatibility view no longer contradicts the per-node migration checks.
- A failed load in a form (networks, ISO images) is shown instead of silently disabling the control.
- Dashboard refreshes that started before a local change no longer undo it (a deleted VM coming back, a started VM shown stopped); reads have a 30 s time limit; terminals stop retrying after a refusal.
- The ISO kernel command line lost by an earlier change is restored; path checks refuse symbolic links leading outside their directory.

- Live-migrating an HA-protected VM no longer disables its protection: the HA record now follows the VM to its new node.
- A good update could be rolled back when the previous process took long to close its connections; the dashboard then reported a success and the update check said "up to date". `/health` now reports the version the process started with, the watchdog waits for it, the service stops within 5 s, and a rolled-back update is detected and can be applied again.
- The update check no longer says "up to date" when the Hyperlite APT source is missing or points to the old address.
- Backups and exports refuse a VM with ZFS or iSCSI disks with a clear message, instead of failing with "No disk found" or silently leaving the block disk out.
- Without the ZFS kernel module (refused by Secure Boot) no `zpool`/`zfs` command runs any more; each one made the kernel log an error every few seconds.
- "Stay signed in" is kept after changing your own password.

- The mirror publication script no longer fails on a CI runner that has no git identity.
- The gh-pages APT mirror no longer serves a stale signed Release: the post-merge hook inherited Git variables that hid the changes of Release, InRelease and Release.gpg, so apt reported "File has unexpected size". The mirror is now published by a script that clears them, compares contents and verifies the result.
- Two administrators can no longer start two updates at once (HTTP 409 naming who started the running one); publishing takes a lock so simultaneous `git pull` runs on the build host queue up.
- Update backups no longer include uploaded ISOs, VM backups, exports, templates or imported disks (each backup was ~12 GB), and only the newest 3 are kept.
- Update rollback: the watchdog now survives the service restart (transient systemd unit) and restores the backup over the real files instead of a nested copy.
- `/health` reports `hyperlite_version`; the update dialog no longer reports success when the release was rolled back.
- A throwaway development admin password file (data/initial-admin-password.txt) had been committed by mistake and was shipped in the package; it is removed from Git and ignored.
- The package now depends on cloud-image-utils, genisoimage and wget, which VM creation needs (a clean install could not create VMs).
- Automation job buttons and the snapshot Restore button now have per-item accessible names.
- Remaining French error message in the email notification sender.
- A wrong code when enabling two-factor authentication, or a wrong password when disabling it, now returns HTTP 400 instead of 401, so the dashboard no longer treats it as an expired session.
- Webhook URLs are validated when the channel is created, not only when a notification is sent.

### Added

- ISO checksum and signature, build information recorded on installed systems, and `scripts/release.sh` for immutable versioned releases.

- Automated tests (pytest), linting (ruff, ESLint), formatting checks and a CI workflow.
- Project documentation, contribution guide, security policy and license (PolyForm Noncommercial 1.0.0).
- Trust-on-first-use SSH host key checking for cluster nodes.

### Security

- A directory storage pool could be created on a system directory (`/etc`, `/usr`…), listing its files as deletable volumes. (#359)
- A manual backup accepted any target directory, written as root, from any VM manager: the directory is checked, and only administrators choose one. (#361)
- Access rights could be given to an account that does not exist, and stayed after an account was deleted: both went to the next account created under that name. (#364)
- The output of automation runs (root commands on the host) was readable by every account: administrators only. (#365)

- The brute-force lock is stored in the database, so a restart no longer resets it; knowing the password no longer allows unlimited guesses of the 2FA code; removing 2FA needs the password and a current code, from a signed-in session only (never an API token).
- A password change or reset signs out every other session of the account.

- Replaced `python-jose` (and its vulnerable `ecdsa` dependency) with `PyJWT`; hardened OIDC ID token validation (algorithm allow-list, required claims, clock leeway).
- Replaced the unmaintained `passlib` with `bcrypt` and `openssl passwd` for password hashing.
- Upgraded React Router and Vite to versions without known advisories.
- Notification channel configuration (webhook URLs, SMTP settings) is no longer readable by non-administrators.
- URLs supplied by users (webhooks, SSO issuer, registries) must be `http(s)`.
