# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions published to the APT repository were timestamp based (`YYYY.MM.DD.HHMM`) until 1.0.0; from 1.0.0 they follow [Semantic Versioning](https://semver.org/): the release PR sets `VERSION`, renames `[Unreleased]` to the version, and adds its French notes in `docs/release-notes/fr/<version>.md`. The section of each version is the release note shown before updating (see `docs/design/updates-1.0.md`).

## [Unreleased]

### Added

- One update per node: Hyperlite and the node's Debian packages together, from the account menu. The dialog (French and English) shows the versions, the Debian packages with the security ones counted, the release notes of every version in between, and whether the node will need a reboot, before anything starts; running VMs are not stopped, and a Hyperlite version that does not start is put back automatically. (#393)
- Going back to the version before the last update of Hyperlite, from the same dialog: its package is kept on the node (so it also works offline) and installed again under the same safety net; one step back, Debian packages unchanged. (#395)
- Debian's security updates every night (03:30 by default, changeable per node on Node › Updates, or turned off): security packages only, never Hyperlite, never an automatic reboot; a pending reboot is notified once per boot. Turned on with this update on nodes that never chose. (#394)
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

- All repository content, user-facing text, logs and error messages are now in English. API and database wire identifiers and values are unchanged (French) for compatibility.
- The web dashboard requires Node.js 20.19+ to build (React Router 7, Vite 7).
- The legacy vanilla-JS frontend (`/legacy`, `/static`) was removed; the React dashboard is the only interface.
- The APT repository location used by the appliance installer is configurable (`HYPERLITE_APT_URL`) instead of being hard-coded.

### Changed (installer)

- The appliance ISO embeds the Hyperlite package and repository key and installs from them, so an installation no longer fails when the APT repository is unreachable or transiently inconsistent.
- The appliance installer no longer sets a default root password: it asks for one, as the Proxmox installer does. The Hyperlite `admin` account gets the random initial password generated by the package. The package no longer changes the Linux root password.
- The local host is always labelled `local`; rows still using a legacy label are migrated at start-up.

### Fixed

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
