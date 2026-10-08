# Design: updates for 1.0.0

Status: **accepted** on 2026-10-03, with the decisions of section 10. The goals below are those Antho chose the
same day (#354).

## 1. Where we are

- **Hyperlite** is a Debian package served by the APT mirror (`installer/apt-source.conf`). `GET /update/check`
  compares the installed version with the APT candidate; `POST /update/apply` backs up the code and database,
  runs `apt-get install --only-upgrade hyperlite`, then restarts the service under a detached watchdog
  (`scripts/update_watchdog.sh`) that restores the backup when the new code does not answer `/health`.
- **Debian** updates are a separate page: `GET /host/updates` lists the upgradable packages, `POST
  /host/updates/upgrade` installs the chosen ones. Nothing says when a reboot is needed.
- `app/core/update_check.py` checks once a day and sends the `update_available` notification.
- Versions are dates (`2026.10.03.2003`), stamped by `scripts/ci-publish.sh` at each push to `master`. The pool
  keeps every published package, so an older version can still be installed by hand.
- Each node is updated by itself, from its own dashboard. Nothing coordinates a cluster.

## 2. Goals

1. **Versions** `1.x.y` (semver), starting at 1.0.0.
2. **One button** per node that updates Hyperlite and Debian together, says beforehand whether a reboot will be
   needed, and offers it afterwards.
3. **Release notes** shown before updating, in French and English.
4. **Return to the previous version** from the dashboard.
5. **Channels**: `stable` (default) and `test`.
6. **Automatic Debian security updates** at night; Hyperlite itself is never updated automatically, only
   announced.
7. **Offline update** from a signed file, for a node without Internet access (the offline ISO's sibling).
8. **Rolling cluster update**: node after node, each emptied by live migration first.

Non-goals: updating a node's Debian major release (12 → 13), automatic reboots, updating the guests.

## 3. Versions

- The version lives in a committed file, `VERSION`, changed by the release PR (`test` → `master`) that states
  the bump: fixes only → patch, a new feature → minor, major only when Antho decides. `ci-publish.sh` reads it
  instead of the date, and refuses to publish a version already in the pool.
- The `.deb` version gets the **epoch** `1:` (`1:1.0.0`): without it, apt orders `1.0.0` below `2026.10.03.2003`
  and nothing upgrades. The epoch never changes again.
- The `test` channel publishes each merge into `test` as `1.1.0~test.20261004.1530` (`~` sorts before the final
  `1.1.0`, so a test node moves to the final release by itself).
- `/health` and the dashboard show `1.0.0`, never the epoch.

## 4. Release notes

- `CHANGELOG.md` already has an `[Unreleased]` section. The release PR renames it to `[1.0.1] - date`.
- The changelog is written in English for the repository; the notes shown in the dashboard need French too:
  each version's section gets a French twin in `docs/release-notes/fr/1.0.1.md`, written in the release PR.
- `ci-publish.sh` publishes the notes next to the APT repository (`notes/1.0.1.en.md`, `notes/1.0.1.fr.md`) and
  inside the offline bundle. `GET /update/check` returns the notes of every version between the installed one
  and the candidate.

## 5. One update button per node

`POST /update/apply` (unchanged path) runs one task:

1. Checks: no other update running, enough free space, the node's VMs listed.
2. Backup of the code and database (as today).
3. `apt-get update`, then `apt-get -y full-upgrade` on **every** package, Hyperlite included, with
   `HYPERLITE_SKIP_RESTART=1` as today. Debian's own prompts are answered with the defaults
   (`DEBIAN_FRONTEND=noninteractive`, `--force-confold`).
4. Reboot need: Debian writes `/run/reboot-required` (and `.pkgs`). The task says which packages need it.
5. Restart of the Hyperlite service under the watchdog (as today).

Before the click, `GET /update/check` already returns what will change: Hyperlite's version and notes, the
number of Debian packages (security ones counted apart), and **whether a reboot will be needed**, predicted
from the package names (kernel, libc, systemd, QEMU, libvirt, microcode). After the update, the node page shows
"Reboot needed" until it is done; the reboot button reuses `POST /host/power`, which can stop the VMs first; in a
cluster, the maintenance mode empties the node by live migration instead (section 9).

The Debian page keeps its package-by-package list for the cases that need it.

## 6. Return to the previous version

- The pool keeps every package, and the previous `.deb` is also kept in `/var/lib/hyperlite/previous/` (needed
  offline).
- `POST /update/rollback` installs the previous version with `--allow-downgrades`, under the same watchdog.
- The database schema only grows (columns and tables are added, never removed or renamed): the previous version
  runs on the newer schema. This is already the rule; a test checks that no migration drops or renames.
- Only one step back, and only Hyperlite: Debian packages are not downgraded.

## 7. Channels

- Two APT suites on the mirror: `stable` (from `master`) and `test` (from `test`).
- A node setting (Administration → Updates) writes the suite into `/etc/apt/sources.list.d/hyperlite.list`.
  Default `stable`. Moving from `test` back to `stable` waits for the next stable release (no downgrade).

## 8. Automatic security updates, announced Hyperlite updates

- `unattended-upgrades`, limited to the origin `Debian-Security`, every night at a time set per node (default
  03:30), **never rebooting**. A reboot need is shown and notified (`host_reboot_required`, a new event).
- Hyperlite is excluded from it (`Package-Blacklist`), whatever the channel; `update_check.py` keeps announcing
  new versions (`update_available`).
- A toggle on the Updates page turns the night run off.

## 9. Rolling cluster update

Run from any node, carried by the cluster leader (`cluster_lead.py`), one task per cluster with one step per
node:

1. Preconditions: quorum, every node online, every node on the same channel; the shared storage reachable.
2. For each node, the leader last:
   1. maintenance mode on (`maintenance.py`): its running VMs are live-migrated away (`maintenance.plan`); VMs
      that cannot move (local disks, passthrough) are listed before starting, and the administrator chooses to
      stop them or to skip the node;
   2. the update of section 5 on that node, over the cluster's SSH trust;
   3. reboot if needed, then wait for the node to come back online and answer `/health` with the new version;
   4. maintenance mode off; its VMs are **not** moved back automatically (a later DRS will).
3. Any failure stops the run on the failing node, which stays in maintenance; the nodes already done stay done.

Mixed versions during the run: version N and N+1 must work together in one cluster (replicated configuration and
node-to-node calls). That is a rule for every minor release from now on; a major release may break it and then
says so in its notes.

## 10. Decisions (Antho, 2026-10-03)

1. **Security updates** every night at 03:30 by default (changeable per node), **never an automatic reboot**: a
   reboot need is notified and the administrator picks the moment.
2. **Test channel**: every merge into `test` is published to it.
3. **1.0.0** is published once this chantier and the audit (#354) are done, its exit criteria met. Until then,
   `master` keeps publishing dated versions; the first release under the new scheme is 1.0.0.

## 11. Order of the work

Each step is one PR, tested in the nested lab (three nodes installed from the offline ISO) before merging:

1. Versions and epoch, release notes in CI (sections 3, 4).
2. One update button with the reboot prediction (5).
3. Automatic security updates and the reboot notification (8).
4. Return to the previous version (6).
5. Channels (7).
6. Offline update from a signed file (started on a local branch; manifest signed with the APT key).
7. Rolling cluster update (9), last because it builds on all of the above.
