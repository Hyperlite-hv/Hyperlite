# Design: control plane v2, from one SQLite file to a distributed control plane

Status: **phase 0 (analysis) done; decisions recorded in sections 15.1 and 15.2.** Phase 1 starts with lot 1
(section 16).

This document answers the "control plane v2" brief: a Proxmox-like cluster (consistent replicated configuration,
quorum, distributed locks, node agents, a scheduler, HA with confirmed fencing) built on etcd, without cloning
Proxmox and without writing consensus or distributed storage ourselves.

> **It changes a decision already taken.** `docs/design/cluster-config.md` (section 8) chose option (a), a periodic
> encrypted copy of the database plus a manual `hyperlite promote`, and ruled out option (c), "every node runs
> Hyperlite and a replicated store", as over-engineering that breaks the "no agent on nodes" model. This brief is
> option (c) with etcd. Both maintainers must confirm the change of direction (question Q1, section 15) before
> phase 1; until then, option (a) stays what production runs.

## 1. Where we are (baseline, measured on 2026-09-30)

- **One controller.** A single `hyperlite.service` (FastAPI, uvicorn, one process) on the controller host. Other
  nodes run no Hyperlite process: they are driven over `qemu+ssh://` (libvirt) and plain SSH (14 modules use SSH or
  remote libvirt).
- **One SQLite file** (`HYPERLITE_DB_PATH`, WAL, 30 s timeout): 54 tables created at start-up by
  `app/core/database.py`. Production size: 1.8 MB; `audit_log` 7,753 rows (about 780 per day), `metrics_samples`
  2,416, `storage_samples` 1,421, everything else under 20 rows.
- **libvirt is the source of truth for VMs, containers, networks and storage pools.** Their definitions are not in
  SQLite: SQLite only holds what Hyperlite adds around them (SSH user, OS label, backups, pools, HA, start at boot,
  notes...). This matters for the migration: the future `VirtualMachine.spec` does not exist anywhere yet, it has to
  be derived from libvirt XML.
- **VMs are identified by name** (and node), not by a numeric id. The API (`/vms/{name}?node=`) and all side tables
  key on the name. The brief's `VirtualMachine.metadata.id: 100` is a new concept (question Q4).
- **Background work** runs as 37 threads in the API process (`app/main.py::on_startup`): metrics collector, backup
  scheduler, node poller, configuration copy, HA watcher (dry run), automatic clean-up, update check, boot sequence,
  Kubernetes recovery, and one thread per long task (backups, migrations, clones...).
- **Tasks** (`app/core/tasks.py`) are rows in `tasks` + `task_logs`, started at creation (no queue), cancellable
  through an in-memory registry, closed as interrupted at start-up.
- **HA** is in dry-run mode (`app/core/ha_watch.py`): probes, a majority check against a witness, a per-node fencing
  profile (`app/core/ha_fencing.py`: IPMI, Redfish, AMT or lease only, status query only), libvirt lockd detection.
  It never restarts a VM. Option (a) of `cluster-config.md` is implemented (`app/core/config_copy.py`,
  `scripts/hyperlite-promote`).
- **API**: 229 paths, 292 HTTP operations and 6 WebSockets, none versioned (`/vms`, not `/api/v1/vms`). Largest
  groups: `/vms` 62, `/auth` 34, `/host` 23, `/containers` 18, `/nodes` 17, `/networks` 14.
- **Latency baseline** (throwaway backend on the real host, no VM, 20 requests each): median 13 to 18 ms and p95
  13 to 25 ms for `/health`, `/vms`, `/containers`, `/storage`, `/networks`, `/nodes`, `/tasks`, `/audit`,
  `/dashboard`, `/meta`. Load at 100/500/1,000 VMs has never been measured (phase 9, and a synthetic benchmark in
  phase 2).
- **Available packages** on Debian 13: `etcd-server` / `etcd-client` 3.5.16, `postgresql` 17. Python etcd clients:
  `etcd3gw` 2.7.0 (OpenStack, maintained, HTTP gateway: KV, transactions, leases, watches, TLS); `etcd3` 0.12.0 is
  unmaintained since 2020 and must not be used.

## 2. Inventory of the SQLite tables and their destination

Destinations: **etcd** (small, critical cluster configuration and control state), **etcd+lease** (short-lived state
every API instance must see, expiring by itself), **PostgreSQL** (history, search), **Prometheus/Loki** (metrics,
logs), **local SQLite** (per node, rebuildable), **secret** (a secret value: never in clear text anywhere; see
section 2.2).

| Table | Content | Destination | Notes |
|---|---|---|---|
| users | accounts, role, auth source | etcd `rbac/users` | `hashed_password` is a hash (allowed); `totp_secret` → secret |
| custom_roles | custom roles | etcd `rbac/roles` | |
| acl | assignments | etcd `rbac/acls/<uuid>` | resource ids change with VM ids (Q4) |
| groups, group_members | user groups | etcd `rbac/groups` | members inside the group spec |
| pools, pool_members | VM pools | etcd `pools` | members inside the pool spec |
| api_tokens | token hashes | etcd `rbac/tokens` | `last_used_at` → local, flushed to PostgreSQL (hot write) |
| webauthn_credentials | security keys | etcd `rbac/users/<u>/keys` | `sign_count` is written at each sign-in: CAS required |
| webauthn_challenges | pending challenges | etcd+lease (TTL 5 min) | any API instance must see them |
| sso_login_state, sso_handoffs | OIDC state | etcd+lease | same |
| revoked_sessions | logged-out JWT ids | etcd+lease (until token expiry) | a logout must hold on every API instance |
| login_failures | lock-out counters | etcd+lease | or per-instance (weaker): Q9 |
| sso_config, ldap_config | auth settings | etcd `cluster/auth` | `client_secret`, `bind_password` → secret |
| app_settings | key/value settings | etcd `cluster/settings` | |
| nodes | registered nodes | etcd `nodes/<n>/spec` | + `status` and `lease` written by the agent |
| node_maintenance | maintenance flag | etcd `nodes/<n>/spec` | |
| node_fencing | BMC settings | etcd `fencing/<n>` (FencingProfile) | password → secret |
| node_live | last live figures | etcd `nodes/<n>/status` (minimal) + Prometheus | figures only in Prometheus |
| config_copies | option (a) copy log | local SQLite, then removed | obsolete once etcd is the source |
| ha_protected_vms | HA protection + cached XML | etcd `vms/<id>/spec.availability` | the cached XML goes away: the spec is the definition |
| ha_settings | witness, thresholds | etcd `cluster/ha` | |
| vm_ssh_users, vm_os_label, vm_cloudinit | VM side data | etcd `vms/<id>/spec` | SSH keys are public: allowed |
| vm_boot | start at boot, order | etcd `vms/<id>/spec` | |
| vm_auto_cleanup | clean-up threshold | etcd `vms/<id>/spec` | `last_active_at` → status |
| vm_provisioning | install in progress | etcd `vms/<id>/status` | |
| vm_boot_state | last boot id per node | local SQLite (agent) | purely local |
| container_ssh_users, container_storage, container_apps | container side data | etcd `containers/<id>/spec` | IP → IP allocator |
| object_meta | notes and tags | etcd `.../metadata.labels` + annotations | notes up to 20 kB: bound them (Q7) |
| network_firewall | network rules | etcd `networks/<n>/spec` | |
| shared_pools | shared storage | etcd `storages/<s>/spec` | CHAP password → secret |
| k8s_clusters | k3s clusters | etcd `k8s/<c>/spec` | `jeton`, `kubeconfig` → secret |
| backup_jobs, backup_group_jobs | schedules | etcd `backups/policies` (BackupPolicy) | `derniere_execution` → status |
| jobs, job_steps | automation jobs | etcd `automation/jobs` | |
| notification_channels | channels | etcd `notifications` | SMTP password, webhook secret → secret |
| metric_servers | exporters | etcd `cluster/metrics` | token → secret |
| update_check_state | last update check | local SQLite | per node |
| tasks | tasks | etcd `tasks/<id>` while active, PostgreSQL `task_history` after | section 11 |
| task_logs | task log lines | local SQLite (agent) → Loki / PostgreSQL | never in etcd |
| job_runs, job_run_logs | automation runs | PostgreSQL (+ Loki for output) | |
| backups, container_backups | backup catalogue | PostgreSQL `backup_history` + etcd `volumes` for what restore needs | Q8 |
| audit_log | audit | local buffer (SQLite) → PostgreSQL + Loki | must survive a node loss |
| metrics_samples, storage_samples | time series | Prometheus / VictoriaMetrics | removed from SQLite at phase 9 |

### 2.1 Summary

- 42 tables go to etcd (configuration) or etcd with leases (short-lived shared state): all under 20 rows today.
  `node_live` is split: its minimal status goes to etcd, its figures to Prometheus.
- 7 go to PostgreSQL / Loki (history; `tasks` also has a short-lived etcd entry while a task runs), 2 to
  Prometheus, 3 stay local to a node, and one of those (`config_copies`) disappears with etcd.
- 11 columns hold secrets (`totp_secret`, `bind_password`, `client_secret`, SMTP and webhook passwords, fencing
  password, CHAP password, k3s token and kubeconfig, metric server token): today Fernet-encrypted in SQLite with
  `HYPERLITE_ENCRYPTION_KEY`.

### 2.2 Secrets

The brief forbids secrets in clear text in etcd, YAML or logs. Proposal: **envelope encryption in etcd**. The value
is encrypted with a data key; the data key is encrypted with a cluster key held in a root-only file on each control
plane member (distributed at join time over mTLS). Resources carry a `secretRef`, never the value. The API and the
agent decrypt only when they use the secret. A later step can move the cluster key to a KMS or Vault without changing
the resources. Question Q6.

## 3. Direct SQLite access

- `get_conn()` is called at **263 sites in 49 files** (outside `app/core/database.py`).
- **12 routers** run SQL themselves:

| Router | Calls | Tables |
|---|---|---|
| routers/backups.py | 9 | backups, backup_jobs |
| routers/auth.py | 9 | users |
| routers/nodes.py | 7 | nodes |
| routers/jobs.py | 7 | jobs, job_steps, job_runs, job_run_logs |
| routers/containers.py | 6 | container_backups |
| routers/tasks.py | 4 | tasks, audit_log |
| routers/metrics.py | 4 | metrics_samples, storage_samples, tasks |
| routers/audit.py | 3 | audit_log |
| routers/update.py | 1 | tasks |
| routers/sso.py | 1 | users |
| routers/metric_servers.py | 1 | metrics_samples |
| routers/ha.py | 1 | nodes |

- **Business modules** that run SQL (calls): permissions 25, vm_meta 14, backups 12, webauthn_keys 11, tasks 9,
  container_meta 9, sso 7, shared_pools 7, renaming 7, k8s_cluster 7, ha 7, backup_groups 7, vm_boot 6,
  metric_export 6, cluster 6, object_meta 5, notifications 5, jobs 5, ha_fencing 5, api_tokens 5, security 4,
  network_firewall 4, metrics 4, maintenance 4, update_check 3, login_guard 3, ldap_auth 3, ha_watch 3,
  cloudinit_edit 3, audit 3, api_docs 3, twofa 2, config_copy 2, vm_cleanup 1, seed 1, file_restore 1, csv_export 1.
- Cross-cutting writers: `renaming.py` writes into 25 tables; `audit.py` is called on nearly every request
  (asynchronous writer thread).

## 4. Target code layout and repositories

```text
app/
├── domain/        pydantic models: Resource[Spec, Status], metadata (uid, generation, resourceVersion), errors
├── repositories/
│   ├── interfaces.py      Protocols below
│   ├── sqlite/            today's SQL, moved, same behaviour (phase 1)
│   ├── file/              YAML under /etc/hyperlite (phase 3, standalone)
│   ├── etcd/              etcd3gw, CAS, leases, watches (phase 5)
│   └── postgres/          history and audit (phase 9)
├── services/      use cases: validation, RBAC checks, tasks, locks, calls to repositories
├── agents/        hyperlited: reconciler, libvirt adapter, task executor, inventory, status publisher (phase 4)
└── controllers/   HA, VM, node, clean-up controllers with leader election (phases 6 to 8)
```

Rules: a router validates HTTP input and calls one service; a service never imports FastAPI; repositories are the
only code that knows a storage backend. The `get_conn()` import is forbidden in `app/routers/` (a test enforces it
at the end of phase 1).

### 4.1 Repositories to create

Synchronous at first: the whole code base is synchronous (sqlite3, libvirt, threads), and async Protocols over
synchronous SQLite would only add `run_in_threadpool` everywhere. They become async with the etcd backend (phase 5),
behind the same method names (question Q3).

| Repository | Methods | Tables today | Phase |
|---|---|---|---|
| TaskRepository | create, get, list(query), update_status, append_log, logs, cancel_request, close_interrupted | tasks, task_logs | 1 (lot 1) |
| NodeRepository | get, list, create, delete, rename, update_status, set_maintenance | nodes, node_maintenance, node_live | 1 |
| AuditRepository | append, query, export | audit_log | 1 |
| UserRepository | get, get_by_subject, list, create, update (CAS), delete | users, api_tokens, webauthn_*, revoked_sessions | 1 |
| RbacRepository | roles, groups, pools, acls | custom_roles, groups, group_members, pools, pool_members, acl | 1 |
| BackupRepository | policies, catalogue | backup_jobs, backup_group_jobs, backups, container_backups | 1 |
| AutomationRepository | jobs, steps, runs, run logs | jobs, job_steps, job_runs, job_run_logs | 1 |
| VmMetaRepository → VMRepository | side data now; full spec in phase 2 | vm_*, object_meta, ha_protected_vms | 1–2 |
| ContainerRepository | same for containers | container_* | 1–2 |
| SettingsRepository | auth, HA, metrics, app settings | sso_config, ldap_config, ha_settings, app_settings, metric_servers, notification_channels | 1 |
| MetricsRepository | samples (until Prometheus) | metrics_samples, storage_samples | 1, removed in 9 |
| LockRepository | acquire, renew, release | (in memory today: `vm_locks`) | 5–6 |
| AllocatorRepository | next VM id, MAC, IP | (libvirt network XML today) | 6 |
| SecretRepository | put, get, delete by `secretRef` | encrypted columns | 5 |

### 4.2 Services to create

`TaskService`, `NodeService`, `AuditService`, `AuthService` (users, tokens, 2FA, WebAuthn, SSO, LDAP), `RbacService`,
`BackupService`, `AutomationService`, `VmService`, `ContainerService`, `StorageService`, `NetworkService`,
`HaService`, `PlacementService` (phase 7), `SettingsService`. Each wraps what the corresponding `app/core` module and
router do today; the `app/core` modules that only hold SQL become repositories, the ones that drive libvirt become
the agent's adapters in phase 4.

## 5. Order of migration of the existing modules (phase 1)

Smallest blast radius first, and the domain the later phases need first:

1. **Tasks** (lot 1): `tasks.py`, routers `tasks`, `update`, and the task parts of `metrics`. Every long operation
   uses it; phase 7 rebuilds on it.
2. **Nodes**: `cluster.py` (registration part), `maintenance.py`, routers `nodes`, `ha` (node lookup). Phase 4
   (agent leases) builds on it.
3. **Audit**: `audit.py`, routers `audit`, `tasks` (audit part). Keeps its asynchronous writer.
4. **Automation**: `jobs.py`, router `jobs`.
5. **Backups**: `backups.py`, `backup_groups.py`, routers `backups`, `containers` (container backups).
6. **Auth**: `security.py`, `api_tokens.py`, `twofa.py`, `webauthn_keys.py`, `sso.py`, `ldap_auth.py`,
   `login_guard.py`, routers `auth`, `sso`.
7. **RBAC**: `permissions.py`.
8. **VM and container side data**: `vm_meta.py`, `container_meta.py`, `object_meta.py`, `vm_boot.py`,
   `cloudinit_edit.py`, `vm_cleanup.py`, `renaming.py` (becomes one method per repository).
9. **Settings and the rest**: `ha.py`, `ha_watch.py`, `ha_fencing.py`, `network_firewall.py`, `shared_pools.py`,
   `k8s_cluster.py`, `notifications.py`, `metric_export.py`, `metrics.py`, `update_check.py`, `api_docs.py`,
   `config_copy.py`, `seed.py`, `file_restore.py`, `csv_export.py`.

Exit criterion of phase 1: no `get_conn` in `app/routers/`, every current test passes, the contract tests of
section 12 pass, the API behaves as before.

## 6. API compatibility

- The 292 current operations keep their paths, verbs, wire identifiers (French, see `CLAUDE.md`) and status codes
  through phases 1 to 5: the dashboard, the workstation CLI (`cli/`, Go) and any external script use them.
- The versioned API (`/api/v1/...`, spec/status resources, `resourceVersion`, 202 + `taskId`) is **added next to
  it** from phase 2, not substituted. The unversioned routes become thin adapters over the same services, and are
  deprecated only once the dashboard uses `/api/v1` (a deprecation header, then removal after at least one release
  that announces it).
- Conflicts: today a concurrent change silently wins (last write). In `/api/v1`, a stale `resourceVersion` answers
  409. The unversioned routes keep "last write wins" (they read then write under the same lock) so that the dashboard
  does not break.
- Long operations already answer 202 with a task id on some routes (`backups`, `containers/{name}/backups`,
  `host/system/updates/upgrade`...) and synchronously on others (`POST /vms`, clone). `/api/v1` answers 202 + `taskId`
  everywhere; the old routes keep their current behaviour.

## 7. Phases, rollback and size

Sizes: S ≈ one PR of a few hundred lines; M ≈ 3 to 6 PRs; L ≈ 7 to 15 PRs; XL ≈ more than 15 PRs or new
infrastructure. Every phase ships behind a switch until its exit criterion is met.

| Phase | Content | Rollback | Size |
|---|---|---|---|
| 0 | this document, baseline | none needed | S |
| 1 | repositories + services over SQLite, routers without SQL | revert the PRs: same schema, same data, no migration | L |
| 2 | spec/status models, `uid`, `generation`, `resourceVersion`, `/api/v1` next to the current API, numeric VM ids if Q4 says so | additive columns and tables only; `/api/v1` can be turned off; the old routes never depend on it | L |
| 3 | standalone YAML under `/etc/hyperlite` (FileConfigRepository), `hyperlite config validate/export/diff/rollback/doctor` | switch `HYPERLITE_CONFIG_BACKEND=sqlite`; SQLite stays written until the exit criterion (dual write); rollback = the last YAML backup | M |
| 4 | `hyperlited` agent: libvirt adapter moved out of the API, reconciliation, local journal | the API keeps its direct libvirt path behind a switch until the agent is proven on every node | XL |
| 5 | etcd shadow mode: 3-member lab cluster with mTLS, EtcdConfigRepository, dual write + divergence report | stop the dual write; etcd was never read | L |
| 6 | etcd source of truth: CAS, allocators, locks, node leases, quorum refusal, `/etc/hyperlite` generated | export etcd → SQLite/YAML (`hyperlite config export`) and switch the backend back; tested as a drill before the switch | L |
| 7 | scheduler, task workers, idempotency keys, offline migration | tasks fall back to in-process threads (today's path) | L |
| 8 | HA controller with confirmed fencing, exclusive ownership, split-brain tests | HA back to dry run (today's state): the switch exists already | L |
| 9 | PostgreSQL, Prometheus/VictoriaMetrics, Loki, DR docs, load and chaos tests | exporters are additive; SQLite history kept read-only until PostgreSQL is proven | XL |

## 8. Risks

- **Direction change** (Q1): the "no agent on nodes" simplicity is gone; every node needs the agent, certificates
  and upgrades in lock-step. Two-node clusters (the current production) cannot have a 3-member etcd quorum without a
  third member (a witness VM or a small machine): Q2.
- **Name vs id** (Q4): every side table, the ACLs, the dashboard URLs and bookmarks key on VM names. Introducing ids
  touches all of them; keeping names as keys is simpler but makes renames a multi-key operation in etcd.
- **libvirt as today's source of truth**: VM, network and pool definitions live in libvirt on each node. Making etcd
  the source means deriving specs from XML and keeping XML-only features (passthrough, advanced disk options,
  cloud-init ISOs) representable; anything not modelled must stay in the XML (an opaque `domainXMLOverlay`).
- **Synchronous code**: a sync code base plus async repositories doubles the ways to call things. Phase 1 stays
  sync (section 4.1).
- **Behaviour changes hidden in refactors**: phase 1 touches 49 files; each PR moves one domain with contract tests
  written before the move.
- **etcd operations**: TLS certificates per node and service, snapshots, defragmentation, alerting. A small team must
  run it; the installer has to do most of it.
- **Latency**: etcd round trips (a few ms on a LAN, tens over Tailscale) on paths that are SQLite reads today. Reads
  are served from a watch-fed cache in each API instance and agent, writes go to etcd.
- **Tailscale links between nodes** (production today): etcd needs a stable, low-latency network; members across a
  WAN VPN will see leader elections. The control plane network requirements must be written down (Q2).
- **Fencing hardware**: superseded by section 15.2 (rule 5): fencing is done by a watchdog, as on Proxmox, so a node
  without a BMC can still be part of automatic HA. A BMC only makes the recovery faster.

## 9. Tests to write before each phase

- **Phase 1**: contract tests per migrated domain, written against today's code first (status codes, JSON shapes,
  side effects in the database), then kept green through the move; a test that fails when `app/routers/` imports
  `get_conn`; repository tests run against every backend with one shared suite.
- **Phase 2**: model round trips (SQLite row ↔ resource), `generation` increments, 409 on a stale
  `resourceVersion`, concurrent updates, `/api/v1` next to the old routes.
- **Phase 3**: YAML schema validation, atomic write (fsync + rename), a corrupted file detected at start-up, rollback,
  import SQLite → YAML → objects round trip, crash during write.
- **Phase 4**: agent crash during start, stop and migration; node reboot; divergence between libvirt and the desired
  state; a VM not assigned to the node is never started; duplicated task.
- **Phase 5**: dual-write divergence report; loss of an etcd member; interrupted watch and resync; restore from
  snapshot.
- **Phase 6**: CAS, exclusive lock, lease expiry and renewal, resync after compaction, refusal of mutations without
  quorum, allocator uniqueness under concurrency.
- **Phase 7**: idempotency keys, retry after failure, worker limits, boot storm, 1,000 VM objects with pagination.
- **Phase 8**: network partition, node really off, node alive but isolated, fencing success and failure, volume
  unreachable, no failover without fencing, no failover without quorum.
- **Phase 9**: mTLS everywhere, unauthorised etcd writes refused, secrets never logged or written in clear text,
  certificate rotation, load at 100, 500 and 1,000 VMs.

A phase is not done when unit tests pass: phases 4, 6 and 8 need a 3-node lab (nested or real) and a written
record of the failure drills.

## 10. New dependencies proposed

| Dependency | Why | Phase | Packaging |
|---|---|---|---|
| `etcd-server`, `etcd-client` 3.5 | control plane store | 5 | Debian 13 package |
| `etcd3gw` (Python) | KV, transactions, leases, watches over etcd's HTTP gateway, TLS | 5 | pip, pinned; maintained by OpenStack |
| `pyyaml` or `ruamel.yaml` | YAML projection and standalone config | 3 | pip (ruamel keeps comments: preferred) |
| `grpcio` + `protobuf` | internal agent API with mTLS | 4 | pip; or HTTPS+mTLS with FastAPI first (Q5) |
| `postgresql` 17 + `psycopg` 3 | audit and history | 9 | Debian package + pip |
| Prometheus exporter (`prometheus-client`) | metrics | 9 | pip (the existing InfluxDB/Graphite export stays) |
| VictoriaMetrics or Prometheus, Loki | metrics and logs storage | 9 | external services, optional |
| `fence-agents` | already used (fencing status) | 8 | Debian package, already a dependency |

No consensus, no distributed storage, no Raft of our own (rules of the brief).

## 11. Tasks (phase 7 target, prepared in lot 1)

Today: `create_task()` writes a row with `statut` in `en_attente`, `en_cours`, `termine`, `echec` (French wire
values); a cancelled task ends as `echec` with `annule_par` set; cancellation requests go through an in-memory
registry per process; interrupted tasks are closed at start-up. 127 call sites create, update or log tasks. The target
states (`Pending`, `Scheduled`, `Running`, `WaitingForLock`, `WaitingForFence`, `Succeeded`, `Failed`,
`CancelRequested`, `Cancelled`) map onto the current wire values for the old routes (`Pending`, `Scheduled`,
`WaitingForLock`, `WaitingForFence` → `en_attente`; `Running`, `CancelRequested` → `en_cours`; `Succeeded` →
`termine`; `Failed` → `echec`; `Cancelled` → `echec` + `annule_par`), and are exposed as-is in `/api/v1`. Lot 1 prepares this with a
`TaskRepository` that already accepts an `idempotency_key` column (nullable, unused by the old routes).

## 12. Contract tests (phase 1)

For each migrated route: the status code, the JSON keys and types, the permission required (403 for an observer),
the audit entry written, and the database rows before and after. They are written **before** the move, run against
today's code, and must pass unchanged after it.

## 13. What stays as it is

FastAPI, React/Vite, libvirt/QEMU-KVM and LXC, RBAC/ACL, API tokens, LDAP, OIDC, TOTP, WebAuthn, audit, backups and
restores, snapshots, clones, ZFS/NFS/iSCSI, networks and firewall, metrics export, the tests, and SQLite for local,
rebuildable data.

## 14. Size of the whole programme

Phases 1 to 3 are refactors and additions inside the current architecture (roughly 25 to 35 PRs). Phases 4 to 8
change the architecture and need a 3-node lab (roughly 40 to 60 PRs plus lab work). Phase 9 is operations. In our
current rhythm this is several months of work, not weeks; the order above keeps production usable after every PR.

## 15. Questions that need a decision

- **Q1. Change of direction.** This brief is option (c) that `cluster-config.md` ruled out. Do both maintainers
  confirm etcd, one agent per node, and the end of the "no agent on nodes" model? Option (a) keeps running until
  phase 6.
- **Q2. Quorum hardware.** Production has two nodes linked by Tailscale. etcd needs 3 members on a stable network:
  which third member (a witness VM on a third machine, a Raspberry Pi, a cloud VM)? Is Tailscale acceptable for the
  control plane network, or do we require a LAN or a dedicated VLAN?
- **Q3. Sync or async.** Keep repositories synchronous in phase 1 (recommended, see 4.1) and move to async with
  etcd, or async from the start as written in the brief?
- **Q4. VM identity.** Introduce numeric VM ids (`100`, Proxmox style) with the name as a label, or keep the name as
  the key and add a `uid`? Ids change URLs, ACLs and every side table.
- **Q5. Agent protocol.** gRPC with mTLS (as in the brief) or HTTPS + mTLS with FastAPI for the agent's internal API
  first (one stack, same tooling), with gRPC later if needed?
- **Q6. Secrets.** Envelope encryption in etcd with a cluster key on each control plane member (section 2.2), or a
  separate secret store (Vault / OpenBao) from the start?
- **Q7. Notes and tags.** Notes are up to 20 kB today. Keep them in etcd (bounded to, say, 4 kB), or move long notes
  to PostgreSQL?
- **Q8. Backup catalogue.** PostgreSQL for history, but a restore must work when PostgreSQL is down: keep a copy of
  the catalogue next to the backup files (a manifest per backup), or require PostgreSQL for restores?
- **Q9. Sign-in lock-out.** Cluster-wide counters in etcd (one write per failed sign-in), or per API instance (weaker
  but no etcd write on the sign-in path)?
- **Q10. Standalone mode.** Should a single-node installation keep working with no etcd at all (YAML files, phase
  3), as the brief says, or do we always install a one-member etcd to have one code path?

### 15.1 Decisions (maintainer, 2026-09-30)

- **Q1**: the change of direction is accepted: etcd, one agent per node, the Proxmox-like model. Option (a) keeps
  running in production until phase 6. To be confirmed by the second maintainer.
- **Q3**: repositories are **asynchronous from phase 1**. The SQLite implementations run their queries in a worker
  thread (`asyncio.to_thread`); the synchronous code that remains (background threads, libvirt helpers) reaches them
  through a small synchronous bridge, removed as each caller becomes asynchronous.
- **Lot 1 domain**: **Node**, following Proxmox's architecture, where cluster membership is the foundation that
  quorum, leases and ownership build on.
- Q2, Q4 to Q10 are open; none blocks phase 1.

### 15.2 Decisions (second maintainer, 2026-10-01): Proxmox VE's architecture, Proxmox VE's stack

The second maintainer confirms the move to a distributed control plane and asks for **the same architecture as
Proxmox VE, built on the same foundations**: Corosync and a replicated configuration file system of our own, in the
place of etcd. Every rule below comes from the Proxmox VE documentation (`pmxcfs.adoc`, `pvecm.adoc`,
`ha-manager.adoc`, `pvestatd.adoc`, `pveproxy.adoc`, `pvedaemon.adoc` and `pvescheduler.adoc` in the
`proxmox/pve-docs` repository).

**This supersedes etcd everywhere in this document** (sections 2, 4, 7 phases 5 and 6, 10, and the etcd answers of
Q2, Q6 and Q10). Those sections must be rewritten before phase 5; phase 1 (repositories) is unaffected and
continues: the repositories become the seam where the new store plugs in.

#### 15.2.1 Service map

| Proxmox VE | What it does (Proxmox documentation) | Hyperlite |
|---|---|---|
| Corosync | "reliable group communication"; one vote per node; quorum | **Corosync itself**, the Debian package, configured by Hyperlite |
| `pmxcfs` | "a database-driven file system for storing configuration files, replicated in real time to all cluster nodes using corosync"; SQLite on each node; "Read-only when a node loses quorum"; 128 MiB limit | **`hyperlite-cfs`**, written by us: a SQLite database on every node, every write delivered in the same order to all nodes through Corosync, state transfer when a node joins, writes refused without quorum |
| `pvestatd` | "queries the status of VMs, storages and containers at regular intervals. The result is sent to all nodes in the cluster" | **`hyperlite-statd`**: one bulk libvirt query per node (`getAllDomainStats`), broadcast over Corosync; the API answers from this table and never queries every VM on a request |
| `pveproxy` | the whole API over HTTPS, unprivileged user; "Requests targeted for other nodes are automatically forwarded to those nodes" | **`hyperlite-proxy`**: today's FastAPI app without root rights, on every node, forwarding a request for another node to that node |
| `pvedaemon` | the API's privileged operations, as root, on a local address only | **`hyperlite-daemon`**: the libvirt, ZFS, storage and network actions, root, local socket only |
| `pve-ha-crm` / `pve-ha-lrm` | cluster and local resource managers; self-fencing with a watchdog | **`hyperlite-ha-crm`** / **`hyperlite-ha-lrm`**, watchdog through `softdog` or a hardware watchdog, 60 s |
| `pvescheduler` | starts scheduled jobs (backups, replication) | **`hyperlite-scheduler`** |
| QDevice (`corosync-qnetd`) | a third vote for two-node clusters, "almost configuration and state free" | **`corosync-qnetd` itself**, unchanged |

#### 15.2.2 Rules

1. **One vote per node; no writes without quorum.** A node outside the majority refuses every configuration change
   and every action that needs one (create, start, migrate, change a setting) with a clear message. A quorum loss
   alone does not stop running VMs; only the watchdog of rule 5 does, and only on a node running HA-managed VMs.
2. **The same stack at every size (Q10).** A single node runs `hyperlite-cfs` too, in local mode, as `pmxcfs` does.
3. **Two nodes, as on Proxmox (Q2).** Losing either node leaves the other read-only. The survivor can be made
   writable with a command equivalent to `pvecm expected 1`, which refuses while the other node answers and asks for
   a typed confirmation. For real availability, a QDevice on any third machine: Proxmox "recommend[s] it for 2 node
   clusters". As a QDevice holds no data, nothing secret ever sits on the witness.
4. **Network.** Proxmox: "a reliable network with latencies under 5 milliseconds (LAN performance) between all
   nodes"; above about 10 ms with more than three nodes, stability "gets rather unlikely". The installer measures the
   latency between nodes and refuses to form a cluster above 10 ms. **Nodes linked over the Internet through a VPN
   (Tailscale, WireGuard) cannot form a cluster**; they stay separately managed. A QDevice may sit further away: it
   "isn't limited to the low latencies requirements of corosync".
5. **Automatic HA needs three votes and shared storage; fencing by watchdog.** Proxmox requires "at least three
   cluster nodes" and "shared storage for VMs and containers", and fences by self-fencing: a node without quorum
   "cannot reset the watchdog" and is reset "after the watchdog has timed out (this happens after 60 seconds)". The
   survivors restart its VMs only after that. No BMC is needed; the IPMI, Redfish and AMT profiles of
   `ha_fencing.py` stay an optional extra.
6. **Any node manages the whole cluster.** Signing in on any node gives the whole cluster, as with `pveproxy`. The
   single controller of option (a) disappears; option (a) stays in production until `hyperlite-cfs` replaces it.
7. **Numeric VM ids (Q4): accepted.** Cluster-wide numeric ids allocated by the backend (`100`, `101`...), with the
   name as a label, from phase 2. URLs, ACLs, backups and every side table move to the id.

#### 15.2.3 Scale target: what Proxmox handles

Proxmox has "no explicit limit for the number of nodes" and reports clusters "with over 50 nodes in production".
The target is the same order: **dozens of nodes and thousands of guests**, with every list page answered from the
status table of `hyperlite-statd`, in under 200 ms for 1,000 VMs.

Baseline measured on 2026-10-01 on one node with 1,000 stopped VMs: `GET /vms` takes **3.5 to 4.2 s**. Every VM costs
three XML reads (`XMLDesc`), `info()`, `isActive()` and two SQLite queries that each open a connection. A running VM
adds the guest agent and DHCP lease queries. On a remote node every libvirt call is an SSH round trip. The fixes that
do not wait for the new stack (one bulk `getAllDomainStats`, one XML parse per VM, one SQLite query per table instead
of per VM) can ship before phase 1 ends.

#### 15.2.4 What `hyperlite-cfs` means for the rules of the brief, stated

The brief says "no consensus, no distributed storage of our own". Corosync provides the membership, the quorum and
the totally ordered delivery (the consensus part); `hyperlite-cfs` is the replicated database on top of it, the part
Proxmox wrote as `pmxcfs`. That part is ours to write and to prove: ordered apply, state transfer on join, a node
coming back after a partition, a write during a membership change. `pmxcfs` is AGPL-3.0 and cannot be copied into
Hyperlite (PolyForm Noncommercial); only its documented behaviour is reused. Debian ships Corosync's C libraries
(`libcpg`, `libquorum`, `libvotequorum`) but no Python binding for them, so `hyperlite-cfs` is a small C or Rust
daemon; this choice is open (Q11). Its test plan (partitions, node restarts, concurrent writes, a 1,000-VM configuration) is a gate before any
production use.

- **Q11. Language of `hyperlite-cfs`: C**, like `pmxcfs`, using Corosync's libraries directly (decided 2026-10-01).
  Its design is `docs/design/hyperlite-cfs.md`.
- **Q6. Secrets: as Proxmox** (decided 2026-10-01). Proxmox keeps them under `/etc/pve/priv/` and
  `/etc/pve/nodes/${NAME}/priv/`, "only accessible by root". `hyperlite-cfs` has the same two root-only trees,
  replicated to every node; the values stay encrypted with Hyperlite's key as they are today.

- **Production (decided 2026-10-01)**: the two production nodes sit on two distant sites linked by Tailscale, above
  Corosync's latency limit (rule 4). **Each site becomes its own cluster**, as it would with Proxmox, and VMs can still
  be migrated from one site to the other.

Still open: Q5, Q7 to Q9.

## 16. Lot 1: the Node domain

- `app/domain/node.py`: `Node` with `NodeSpec` (name, host name, SSH user and port, maintenance) and `NodeStatus`
  (reachability, last check, live figures), plus `metadata` (`uid`, `generation`, `resourceVersion`) prepared for
  phase 2 but not exposed by the current routes.
- `app/repositories/interfaces.py`: `NodeRepository` Protocol (async): `get`, `list`, `create`, `delete`, `rename`,
  `update_status`, `set_maintenance`, `clear_maintenance`; `acquire_lease` is declared and raises
  `NotImplementedError` until phase 6.
- `app/repositories/sqlite/nodes.py`: today's SQL on `nodes`, `node_maintenance` and `node_live`, moved from
  `app/core/cluster.py`, `app/core/maintenance.py`, `app/core/metrics.py` and the routers, behaviour unchanged.
- `app/services/node_service.py`: registration, removal, rename, maintenance, listing with live figures.
- `app/core/cluster.py` and `app/core/maintenance.py` keep their functions as thin synchronous wrappers over the
  service, so that their callers (libvirt connections, the metrics collector, HA) do not change in this lot.
- Routers `nodes` and `ha` stop importing `get_conn`.
- Tests: contract tests for `/nodes` (list, add, delete, rename, maintenance, summary) written first against today's
  code; a shared repository test suite; a test listing the routers that still import `get_conn` (an allowed list
  that shrinks with each lot).
- No etcd, no new dependency, no schema change.
