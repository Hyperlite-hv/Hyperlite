# Design: replicated cluster configuration, with quorum

Status: **proposal, waiting for the maintainer's decision.** No code is written before it is accepted.

## 1. Where we are

- Hyperlite runs on **one controller host**. The other nodes have no Hyperlite process: the controller drives
  them through libvirt over SSH (`qemu+ssh://`) and plain SSH.
- All of Hyperlite's own state lives in **one SQLite file** on the controller (`app/core/database.py`,
  `HYPERLITE_DB_PATH`), in WAL mode. VMs, networks and pools are **not** in it: libvirt on each node stays the
  source of truth for them.
- Encrypted values in the database (SMTP password, OIDC client secret, soon the fencing credentials) use the
  Fernet key `HYPERLITE_ENCRYPTION_KEY` from `.env`. Session tokens are signed with `HYPERLITE_SECRET_KEY`.
  A copy of the database is useless without these two keys.
- When the controller is down, the VMs on the other nodes keep running (libvirt does not need Hyperlite).
  But there is no UI and no API, and nothing runs that needs the controller: HA detection, scheduled backups,
  automation jobs, notifications.

What the database holds, by kind:

| Kind | Tables (examples) | Write rate | Losing the last minutes |
|---|---|---|---|
| Configuration | users, custom_roles, acl, groups, pools, nodes, node_maintenance, ha_protected_vms, backup_jobs, jobs, notification_channels, sso_config, network_firewall, vm_auto_cleanup, api_tokens | Rare (admin actions) | Unacceptable: a user or permission change silently lost |
| Operations | tasks, backups, container_backups, job_runs, audit_log, login_failures | Frequent (almost every request writes an audit entry) | Tolerable for tasks; audit gaps are regrettable |
| Telemetry | metrics_samples, node_live, storage_samples | Continuous (every 15 s) | Harmless |

## 2. Goal

- The cluster stays manageable when the controller host fails: another node can take over with the same
  users, permissions, nodes, HA settings, schedules and secrets.
- **Never two active controllers at once.** Two controllers would each fence, restart, back up and write their
  own view: split brain, the same danger as in the HA design.
- Writes are refused, or the controller steps down, when it cannot see a majority.

## 3. Options

### (a) Periodic copy of the database to the other nodes

The controller takes a consistent snapshot with SQLite's online backup API (`sqlite3.Connection.backup`,
safe while the service writes). Every N minutes, and right after any configuration change, it pushes the snapshot,
encrypted, to each node over the existing cluster SSH key. Takeover is **manual**: a documented
`hyperlite promote` on the chosen node installs the snapshot and starts the service.

- **Cost:** small (a periodic job, a promote script, docs). Nodes only need the Hyperlite package installed
  and stopped.
- **Risks:**
  - loses what changed since the last copy (bounded: minutes for operations, ~0 for configuration thanks to
    the copy-on-change);
  - an admin could promote while the old controller is still alive; the script mitigates this by checking
    that the old one is unreachable and by fencing it if fencing exists.
- **For HA:** no automatic restart while the controller is down. HA resumes on the promoted controller with
  the copied state.

### (b) Continuous replication (Litestream-style) with primary/standby failover

A standby node runs Hyperlite in **passive mode**: read-only, no background loops. It continuously receives
the WAL of the primary, either with Litestream (`litestream replicate` to an SFTP or file target on the
standby, `litestream restore` on promotion) or with a small WAL shipper of ours. Failover promotes the
standby; it can be automatic **only** with a witness and fencing of the old primary (the rules of the HA
design).

- **Cost:** medium. A passive mode in the app, which disables writes and background loops. Replication
  packaged and monitored (lag alert). The promotion logic with witness and fencing. A floating address or DNS
  change for the UI.
- **Risks:**
  - async replication loses a few seconds on failover;
  - automatic promotion without reliable fencing is the split brain we must avoid, so the first version keeps
    promotion manual, with one command.
- **For HA:** the HA engine runs only on the active controller. After promotion, it resumes from a state
  seconds old. With a witness and fencing, controller loss and node loss can both be handled.

### (c) The full Proxmox model (pmxcfs-like)

Every node runs Hyperlite and a replicated store. Writes need a majority (Raft/Corosync). Every node can serve
the UI. Candidates to avoid writing consensus ourselves are **dqlite** (Raft-replicated SQLite, used by LXD)
and **rqlite** (Raft SQLite over HTTP).

- **Cost:** large.
  - Every node runs Hyperlite, a real change to the "no agent on nodes" architecture.
  - All database access (`get_conn()`, hundreds of call sites) must go through the replicated store, with its
    own semantics (no local file, network round trips, a single writer through the leader).
  - The background loops (poller, scheduler, HA) need leader election.
  - At least 3 voting members: two nodes plus a witness or qdevice.
- **Risks:**
  - a large rewrite;
  - a new failure class (lost quorum: writes refused, the UI read-only);
  - telemetry must stay local, or it will saturate consensus.
- **For HA:** the best base. The HA engine runs on the elected leader, quorum is built in, and a node that
  loses quorum knows it must not act (and can self-fence with a watchdog, as Proxmox does).

## 4. Recommendation

Go **(a) → (b)**, and keep (c) as a long-term option only if the cluster grows beyond a handful of nodes.

1. **(a) now**: it removes the "controller host lost = configuration lost" risk at a low cost, and it gives the
   promotion procedure that (b) will reuse.
2. **(b) next**: a standby with continuous replication and a one-command, manual promotion. Automatic
   promotion comes only once the HA design's fencing and witness exist.
3. Keep telemetry (`metrics_samples`, `node_live`, `storage_samples`) out of the replicated set, or send it at
   a low frequency: it is most of the write volume and has no value after a failover.
4. **Keys**: the two keys from `.env` travel with the snapshot, encrypted with a per-cluster key exchanged at
   node registration, never in clear text. Without them the copy cannot decrypt its secrets.

(c) would be over-engineering for a two or three node homelab or small business setup, and it breaks the "no
agent on nodes" model, which is one of Hyperlite's simplifications.

## 5. What each option means for HA

| | (a) copy | (b) standby | (c) replicated store |
|---|---|---|---|
| Controller lost | No HA until an admin promotes | HA resumes after promotion (manual, then automatic with fencing and a witness) | HA continues on the new leader |
| Split brain protection | Promote script checks the old controller | Fencing plus a witness required for automatic promotion | Quorum built in |
| Data lost on failover | Minutes (operations), ~0 (configuration) | Seconds | None for committed writes |

## 6. Steps for the recommendation

1. Snapshot and push (a): a `cluster_config_sync` job, copy-on-change for configuration tables, encrypted
   transfer, and freshness shown on the Nodes page ("configuration copy: 2 min ago").
2. `hyperlite promote` (a): checks (old controller unreachable, snapshot age), install, start; a docs page with
   a drill.
3. Passive mode in the app (b): read-only API, no background loops, a banner "standby of <primary>".
4. Continuous replication (b): Litestream or our WAL shipper, lag monitoring and an alert.
5. Promotion with a witness and fencing (b), after the HA design's steps 1 to 3.

## 7. Questions for the maintainer

1. Which node would be the standby? Does it have enough disk for the database (the size is dominated by
   telemetry)?
2. Is losing a few minutes of audit and tasks acceptable after a failover (option a), or do we go straight to
   (b)?
3. Should the UI move with the controller (a floating address, Tailscale name), or is a different URL per node
   acceptable?

## 8. Decisions (maintainer)

- **Option (a) first**: a periodic, encrypted copy of the configuration to the other nodes (and right after a
  configuration change), plus a manual `hyperlite promote`. (b) and (c) are not started.
- Losing a few minutes of audit and tasks after a promotion is acceptable; configuration changes are copied at
  once, so they are not lost.
- Not decided yet: which node is the preferred standby (every registered node receives the copy, so any can be
  promoted), and whether the UI address moves with the controller (for now each node keeps its own URL; the
  promote command prints it).

## 9. Superseded direction (2026-09-30)

The maintainer decided to move to a distributed control plane built on etcd, with one agent per node (option (c)
here, with etcd instead of dqlite/rqlite). See `docs/design/control-plane-v2-migration.md`. Option (a) stays what
production runs until phase 6 of that plan.
