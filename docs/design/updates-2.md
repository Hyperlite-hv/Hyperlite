# Design: updates 2 (an update you can follow, and a notification centre)

Status: **accepted**. It builds on [updates-1.0](updates-1.0.md), whose foundations stay: one update per node
(Hyperlite and Debian together), signed APT repository, release notes, channels, return to the previous version,
signed offline file, rolling cluster update, the watchdog that puts the previous version back.

## 1. What was wrong

An audit of `app/routers/update.py`, `app/core/update_check.py`, `scripts/update_watchdog.sh`, the update dialog and
the notifications pane found:

1. **Invented progress.** The job reported 5, 15, 20, 35, 40, 85, 90 %: fixed numbers at fixed steps, not measures.
2. **A success claimed too early.** The task ended `succeeded` before the service restarted. Whether the new code
   started, or the watchdog put the previous version back, was only written in `update.log`; the browser decided by
   polling `/health` for two minutes. Closing the dialog or reloading the page lost the outcome; nothing recorded a
   rollback as such.
3. **The browser followed the update.** The dialog held the state; there was no API to ask "what is the update doing
   now" after a reload or from another browser.
4. **No checks before starting.** Not the free space, not an interrupted dpkg (`dpkg --audit`), not the APT lock,
   not the operations running on the node, not a version going backwards.
5. **Notifications were not kept.** The bell listed what the browser derived from the inventory (offline nodes,
   crashed VMs, full pools) and the running tasks: nothing persisted, nothing read or unread, no history, and no
   update ever appeared there. Update news went only to email and webhook channels.
6. **A fixed hourly check**, with no way to change it, and no difference between an ordinary and a critical update.
7. **No update history**: the tasks and the journal held pieces, not "from which version to which, who, how long,
   with what outcome".

## 2. Target

### 2.1 Update runs (`app/core/update_runs.py`, table `update_runs`)

Each update (online, from a file, return to the previous version) is a **run**: kind, version before and after,
whether Debian is included, who started it, when, its status and its **steps**. Steps have a status (`pending`,
`running`, `done`, `failed`, `skipped`), times, a detail line and, only when it is measured, a percentage.

| Step | What it does | Measured percentage |
| --- | --- | --- |
| `preflight` | the checks of 2.2 | no |
| `backup` | code, configuration and database copied | no |
| `index` | `apt-get update` (or the file checked and read) | no |
| `download` | `apt-get --download-only` | yes, from APT's status channel (`dlstatus`) |
| `install` | `apt-get` installs | yes, from dpkg's status (`pmstatus`) |
| `restart` | the service restarts under the watchdog | no |
| `verify` | the new version answers, or the watchdog put the previous one back | no |

Statuses of a run: `running` → `verifying` (the package is installed, the service restarts) → `succeeded`,
`rolled_back` or `failed`. **The server decides the outcome**: the watchdog writes its verdict to
`update/watchdog-result.json`; at start-up, whichever version runs closes the run that was `verifying`
(`finalize_after_restart`). A run left `verifying` past 15 minutes without a verdict is `failed`.

Downloading apart from installing makes an interrupted download harmless: nothing is installed until every package
is fetched, and APT resumes the partial files on the next attempt.

### 2.2 Checks before starting (`app/core/update_preflight.py`)

Each check is `ok`, `warning` (said, the administrator confirms) or `blocking` (the update does not start):

- free space on `/` and on the backup directory (blocking under 1.5 GiB);
- dpkg interrupted (`dpkg --audit` not empty: blocking, with the command that repairs it);
- the APT/dpkg lock held by another program (blocking);
- an update already running (blocking: the existing 409);
- operations running on the node (warning: an API restart ends what runs inside the API);
- the candidate older than the installed version (blocking) or another major version (warning);
- a node of a cluster (warning: the rolling update keeps the other nodes in step);
- a reboot already pending (warning).

### 2.3 Checking for versions

- Interval chosen by an administrator: off, 1 h, 6 h, 24 h (default 6 h), in `app_settings`.
- The last check's result is kept, so the page shows the status without contacting the mirror each time.
- **Critical** update: the Debian side carries security updates, or a release note has a security section.

### 2.4 Notification centre (`app/core/notification_feed.py`, tables `notification_feed`, `notification_marks`)

Kept on the node, read and unread per account. An entry has a level (`info`, `update`, `warning`, `error`), a
title, a detail, a link (a page of the console), an audience (`admin` or `all`), and may be **pinned**: it stays on
top until what it reports is over (`resolve_key`). Fed by:

- the update runs (started, finished, rolled back, failed) and the version check (available, critical);
- the journal entries that already notify: VM crashed, reboot needed, alert rule fired / resolved, backup failed,
  HA alert, node state change.

API: `GET /notifications/feed`, `POST /notifications/feed/read` (some or all), `POST /notifications/feed/archive`.
The bell shows the unread count; the pane has the notifications, the running operations and the live alerts. New
entries appear once as a toast.

### 2.5 The Updates page

A page of its own (`?tab=updates`), instead of a modal dialog: the state (installed, available, channel, last
check, status), the actions (check now, read the notes, start, return to the previous version, update from a file),
the checks, the run in progress with its steps, the history, the settings, and the technical log of a run.

## 3. What does not change

- Running VMs, storage and configuration are untouched by an update; nothing reboots the host by itself.
- The job runs out of the service (a unit of hyperlite-daemon): closing the browser changes nothing.
- The watchdog, the backup, the previous package kept, the lock against two updates.
