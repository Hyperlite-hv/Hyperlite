# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions published to the APT repository are timestamp based (`YYYY.MM.DD.HHMM`) and generated automatically; this file tracks user-visible changes between them.

## [Unreleased]

### Added

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

- The brute-force lock is stored in the database, so a restart no longer resets it; knowing the password no longer allows unlimited guesses of the 2FA code; removing 2FA needs the password and a current code, from a signed-in session only (never an API token).
- A password change or reset signs out every other session of the account.

- Replaced `python-jose` (and its vulnerable `ecdsa` dependency) with `PyJWT`; hardened OIDC ID token validation (algorithm allow-list, required claims, clock leeway).
- Replaced the unmaintained `passlib` with `bcrypt` and `openssl passwd` for password hashing.
- Upgraded React Router and Vite to versions without known advisories.
- Notification channel configuration (webhook URLs, SMTP settings) is no longer readable by non-administrators.
- URLs supplied by users (webhooks, SSO issuer, registries) must be `http(s)`.
