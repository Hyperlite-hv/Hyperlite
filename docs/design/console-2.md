# Console 2: availability, dashboards, saved queries, patch plans, autoscaling, triggers, scripts, remote desktop, connection diagnosis, assistant

Status: implemented in three releases (1.25, 1.26, 1.27), after the console quick wins of 1.24 (JSON view, tags page,
alert silences, account end dates, high contrast).

These are the daily tools a managed platform gives a team on top of what Hyperlite already has: knowing how
available each machine has been and why, gathering what matters on shared dashboards, keeping guests patched on a
rhythm, letting a group of VMs follow its load, reacting to events without a person, running ready-made scripts in
guests, reaching a Windows desktop from the browser, finding why two machines do not talk, and asking questions in
plain words.

## 1. Availability (release 1.25)

**What.** An "Availability" tab on every VM and node: its state now (available, stopped, degraded, unavailable,
unknown) with the reason, the share of time it was running over 24 hours, 7 and 30 days, a strip of its states over
the period, and the list of interruptions (when, how long, why, who).

**Where the facts come from.** The metrics collector (`app/core/metrics.py`) already reads every VM of this node and of
each online remote node every 15 seconds. At each round it now hands each VM's libvirt state and reason to
`app/core/availability.py` (`observe`), and each node's reachability (this node: its own round; a remote node: its
probe). Only changes are written:

- `availability_state(target PRIMARY KEY, state, reason, since, last_seen)`: the current state of each target and when
  it was last seen. `last_seen` is updated in one statement per round for all targets.
- `availability_events(id, target, state, reason, started_at, ended_at)`: one row per period in one state.

Targets use the metrics' convention: `vm-name` for a VM of this node, `node:vm-name` for one of a remote node, `host`
for this node, `node:<name>` for a remote node. Both tables are history, kept by each node (not replicated, like the
metrics): the node that answers a request has watched the whole cluster.

**States.** `running`, `paused`, `stopped` (shut off on purpose: by Hyperlite, by the guest itself, a migration away,
a saved state), `crashed` (QEMU died, failed to run, or a guest panic: the reasons `vm_crash_watch` already knows),
`unreachable` (a node that does not answer) and `unknown`. A gap longer than three rounds between two observations
(this node was down, or Hyperlite stopped) becomes an `unknown` period: Hyperlite never claims a VM ran while nobody
watched.

**Why and who.** A stop is matched with the journal (`audit_log`): a `stop_vm`, `force_stop_vm`, `restart_vm`,
`start_vm`, a migration, a schedule or an HA move on that VM within a minute names the account. Otherwise the libvirt
reason says it (shut down by the guest, destroyed, crashed...).

**Measures.** Over a period: running time, stopped time, interrupted time (crashed, unreachable, unknown while it had
been running), the share running, and the number of unplanned interruptions. "Available" counts running time over
the time it was meant to run (the period minus the stops made on purpose), so a test VM switched off at night is not
"80 % available".

**API.** `GET /availability/{kind}/{name}?node=&days=` (kind `vm` or `node`; the VM read right, `node.view` for a
node), `GET /availability/summary?days=` (every target in one list, for the dashboard tile and the lists). Events are
kept 400 days.

## 2. Dashboards (release 1.25)

**What.** Named, shared dashboards made of tiles, replacing the single list of charts pinned on Home.

- A dashboard: `dashboards(id, name, owner, shared, tiles JSON, created_at, updated_at)`, replicated. `shared`:
  `private` (its owner only), `everyone` (every account reads it), or a group id (its members read it). Its owner
  and administrators change it; a reader may copy it to make it their own.
- A tile: `{id, type, title, size (1..3 columns), config}`. Types: `chart` (a metrics explorer chart: series and
  range), `query` (a saved query, shown as a count, a list or a bar chart by a field), `availability` (the
  availability of chosen VMs or nodes), `alerts` (rules firing now), `note` (short text), `resources` (counts of VMs
  and nodes by state).
- Pinning: "Pin to a dashboard" on an explorer chart, a saved query, a VM's availability and its performance charts
  opens a small dialog to choose the dashboard (or make one). The charts already pinned on Home in this browser are
  moved into a first private dashboard "My dashboard" once, then Home shows the dashboard chosen as its own.
- A tile is drawn with the reader's own rights: a shared dashboard never shows a VM its reader cannot see.

**API.** `/dashboards` (list what the account can read, create), `/dashboards/{id}` (read, replace, delete), `POST
/dashboards/{id}/copy`.

## 3. Saved queries (release 1.25)

**What.** A search by query (`kind:vm state:stopped tag:prod`) is saved with a name and a view (list, count, or bars
by `state`, `node`, `os`, `pool` or `tag`), private or shared like a dashboard, and opened again from the search page or
shown on a dashboard.

**Where.** `saved_queries(id, name, query, view, group_by, owner, shared, created_at)`, replicated. The query runs in
the browser on what the reader can see (the search page's own engine, `lib/query.js`), so saving one grants nothing.

## 4. Patch plans (release 1.26)

**What.** Recurring windows for guest updates: "every Tuesday at 03:00, three hours, on the pool prod, reboot if
needed". A plan names its VMs (a list, a pool, or a tag), its days of the week (or a day of the month), its time and
time zone, its length, and whether to reboot. At each occurrence `guest_update_plans.tick` (every minute, the access
sweep's loop) resolves the VMs of this node at that moment and schedules a run with the existing engine
(`guest_updates.schedule`), so following, rebooting and the outcome are those of a run made by hand. The page shows
each plan's next occurrences and its past runs.

**Where.** `guest_update_plans(id, name, target_kind, target, days, monthday, at_time, tz, duration_min, reboot,
enabled, last_occurrence, created_by)`, each node's own (as the runs: a node patches its own guests).

## 5. Autoscaling of VM groups (release 1.26)

**What.** A VM group (section 13 of governance.md) may follow its load: a minimum and maximum count, a CPU threshold
to add a VM and one to remove one, a window, a step and a cool-down. Every minute the scheduler reads the average CPU
of the group's running members over the window (the metrics' raw tier); above the high threshold for the whole window
it adds `step` VMs (started), below the low one it removes the highest-numbered member (stopped gracefully, then to
the recycle bin when it is on), never beyond the bounds and never twice within the cool-down. Each decision is
journaled and kept with its reason; the group page shows the last ones.

**Where.** `vm_group_scaling(group_id PRIMARY KEY, enabled, min_count, max_count, up_pct, down_pct, window_min,
step, cooldown_min, last_action, last_action_at, last_reason)`, each node's own like the groups.

## 6. Triggers (release 1.26)

**What.** An automation job runs by itself when something happens: an alert rule fires (any, or one), a VM crashes,
a backup fails, a node stops answering, a guest update run fails. A trigger names the event, an optional filter (the
rule, the VM, the node), the job, whether the VM concerned is passed as the job's target (its "each target" steps),
and a cool-down. `app/core/triggers.py` is called where the event is already journaled (alert rules, crash watch,
backups, HA, guest updates); a firing within the cool-down is skipped and said in the journal. Each firing is kept
(`trigger_firings`) with the run it started. Creating a trigger is an administrator's, as creating a job.

**Where.** `job_triggers(id, name, event, filter, job_id, pass_target, cooldown_min, enabled, last_fired_at,
created_by)` (each node's own: jobs are) and `trigger_firings(id, trigger_id, at, subject, run_id, outcome)`.

## 7. Script library (release 1.26)

**What.** Ready-made scripts run in guests through their agent: install Docker, install the node exporter for
Prometheus, harden SSH (no password, no root), turn on automatic security updates, set the time zone, for Windows:
turn on remote desktop, install OpenSSH Server. Administrators add their own (`sh` or PowerShell, with named
parameters). A run picks scripts and VMs; each guest runs it in the background (the guest updates' way, so a long
installation outlives the agent's two-minute limit), followed every minute, with its exit code and the end of its
output kept. Running a script is `vm.exec` on each VM (the daemon checks it), so a delegate runs scripts only on
their own VMs.

**Where.** `guest_scripts(id, name, description, shell, body, params, builtin, created_by)` (replicated) and
`guest_script_runs(id, script_id, vms, params, status, results, created_by, created_at)` (each node's own).

## 8. Remote desktop in the browser (release 1.27)

**What.** "Remote desktop" on a Windows VM (or any guest with an RDP server) opens the desktop in a browser tab: the
guest's resolution follows the tab, the clipboard works both ways, no client to install.

**How.** The node itself speaks RDP to the guest and shows the result to the browser as the console already does:
hyperlite-daemon starts, per session, a transient systemd unit with its own dynamic user that runs a headless X
server with a built-in VNC server (TigerVNC's `Xvnc`, listening on a Unix socket only) and the FreeRDP client in it,
full screen. The API relays the socket to noVNC in the browser, as for a VM's console. The account name and password
are typed in the page, sent once over the session's WebSocket and handed to FreeRDP on its standard input (never on
a command line). The guest's certificate is trusted on first use and remembered per VM: a changed certificate stops
the connection and says so. Packages: `tigervnc-standalone-server` and `freerdp3-x11`, recommended by the package;
the button says what to install when they are missing. Opening one takes `vm.console` on the VM, sessions end after
30 minutes idle, and each is journaled.

## 9. Connection diagnosis (release 1.27)

**What.** "Can VM A reach B on TCP port P?" answered from three angles at once:

1. Hyperlite's own firewalls on the path (the VM firewall of A outbound and of B inbound, the network firewall), read
   from their rules: the rule that would drop or reject the packet is named, with a link to it.
2. Where they are: both on the same network, addresses, the node of each.
3. A real try from inside A through its agent (a TCP connect with a 5-second limit), and whether B's guest has
   something listening on P (when B has an agent).

The verdict says which of them stops it: a Hyperlite rule, nothing listening, the guest's own firewall, or no route.

**API.** `POST /network/diagnose {from_vm, to (VM name or address), port, protocol}`; `vm.exec` on A to try from it.

## 10. Assistant (release 1.27)

**What.** A side panel where an account asks in plain words ("which VMs crashed this week?", "why is web-2 slow?",
"what does this error mean?") and gets an answer built from Hyperlite's own data, with links to open what it names.

**Rules.**
- Off until an administrator turns it on and chooses where questions go: a model reached through an
  OpenAI-compatible endpoint (a local server such as Ollama or vLLM keeps everything on site) or the Anthropic API.
  The key is kept with the node's secrets. The settings page says plainly what leaves the node.
- The assistant reads only, with the asking account's own rights: its tools are read-only API calls (lists, a VM's
  details, metrics summaries, availability, alerts firing, the journal) made as that account. It never changes
  anything: an action is proposed as a button the person clicks, which goes through the usual dialog.
- Each question is journaled (who, when, which tools were used), not its text unless the administrator asks for it.
- Answers say when they rely on data older than a few minutes.

## Order and checks

- 1.25: availability, dashboards, saved queries.
- 1.26: patch plans, autoscaling, triggers, script library.
- 1.27: remote desktop, connection diagnosis, assistant.

Each step comes with targeted tests (pytest for the engines and routes, vitest for the query and dashboard helpers),
the English and French text, and is tried on the production node after its release: guests on a disposable VM for
scripts, patch plans, autoscaling and remote desktop (an RDP server on a Linux guest).
