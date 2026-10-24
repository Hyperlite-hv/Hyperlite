# API conventions

The HTTP API is what the dashboard and `hyperlite-cli` use. Every route is under **`/api/v1`**: version 1 of the
contract, since Hyperlite 1.0. Requests are authenticated with `Authorization: Bearer <token>`, where the token is a
session JWT (from `POST /api/v1/auth/login`, or the two-step `POST /api/v1/auth/login/2fa`) or a personal API token
(`hlt_...`). An interactive OpenAPI description (`/docs`, `/openapi.json`) is opened from **Administration › API** by
the accounts an administrator chose.

What a browser opens rather than calls stays at the root: `GET /health` (answers everyone with `{"status",
"environment", "api"}`, `api` being the version of the API, 1; the host name, `node` (this node's name in its
cluster, which lists of the cluster call `local`) and software versions only to a signed-in caller), the SSO sign-in
`/auth/sso/login` and the callback an identity provider is given (`/auth/sso/callback`), the client download
`/downloads/hyperlite/{platform}`, and the documentation pages.

Before 1.0 the API had no prefix. Those addresses now answer **410 Gone** with `{"detail": ..., "code": "api_gone"}`
naming the new address (for an old `hyperlite-cli`, asking to update it), never the dashboard page.

## Names

English everywhere (the glossary of the 1.0 renames is `docs/design/api-v1/glossary.md`). Keys are `snake_case`;
times end in `_at` (ISO 8601, UTC), durations in `_s`, sizes in `_bytes`, `_mb` or `_gb`. Values are lowercase English
words:

| Kind | Values |
|---|---|
| VM states | `active`, `stopped`, `paused`, `suspended`, `crashed`, `shutting_down`, `unknown` |
| Task and backup states | `pending`, `running`, `succeeded`, `failed` |
| Audit results | `success`, `failed` |
| Node states | `online`, `offline`, `unknown` |
| Global roles | `admin`, `viewer` |
| Backup frequency and mode | `daily`, `weekly`, `monthly`; `live`, `cold` |

Every list answers `{"items": [...], "total": n}` (`app/core/listing.py`). A list that gathers every node of a cluster
(`?cluster=true`) adds `"unreachable"`, the members that did not answer, and the VM list `"stale"`, the members whose
figures are older than 30 s. The CSV exports take the same `?cluster=true` (`GET /tasks/export.csv`,
`GET /audit/export.csv`): every member's rows, newest first, with a last column `member`, the node that holds the row;
the members that did not answer are named in the `Hyperlite-Unreachable` response header, and a member that stops
answering midway ends the download with an error rather than a shorter file.

Inside v1 a change is only ever an addition (a route, an optional field, a documented new value); anything else waits
for a v2, served next to v1.

### Deprecated

A deprecated route works as before for as long as v1 lasts and is not in v2. It is marked `deprecated` in the API's
description (`/docs`, `docs/api/openapi-v1.json`) and answers with a `Deprecation` header (RFC 9745, the date it was
deprecated) and a `Link` to what replaces it.

| Since | Routes | Instead |
|---|---|---|
| 2026-10-24 | Hosts driven over SSH from one node: `POST /nodes`, `POST /nodes/test`, `GET /nodes/cluster-pubkey`, `GET` and `POST /nodes/config-copy` | A node joins the cluster (`POST /cluster/join`); a site across a WAN, one view over several sites (#656) |

Every object a list gives can also be read alone, with the same fields: `GET /storage/{pool}?node=`,
`GET /storage/{pool}/volumes/{volume}`, `GET /pools/{id}`, `GET /groups/{id}`, `GET /acl/{id}`,
`GET /acl/custom-roles/{id}`, `GET /auth/users/{username}`, `GET /backup-groups/{id}`, `GET /ha/{vm}` (404 when there
is none). `PATCH /acl/custom-roles/{id}` takes `name`, `privileges` or both, and keeps the role's assignments;
`PATCH /pools/{id}` takes `name`, `description` or both.

## Errors

Errors are returned as JSON `{"detail": "...", "code": "..."}` with an appropriate HTTP status. `detail` is for a
person: in English and, for infrastructure failures, categorized into an actionable cause (see
`app/core/error_messages.py`); it may change. `code` is for a script: one of the list below, which only ever grows
(`app/core/error_codes.py`). An error that names nothing more precise has its status's code.

| Code | Meaning |
|---|---|
| `invalid` | The request is not valid: a field is missing or wrong (400, 422). |
| `unauthorized` | No session, or one that expired or was revoked (401). |
| `forbidden` | The account may not do this (403). |
| `not_found` | Nothing by that name or id (404). |
| `method_not_allowed` | The route exists, not with this method (405). |
| `conflict` | The request conflicts with the current state: a name already taken, a VM that must be stopped (409). |
| `too_large` | The upload or body is larger than allowed (413). |
| `locked` | Another operation holds the object (423). |
| `too_many_requests` | Too many attempts: wait before trying again (429). |
| `internal` | An error of the server: its log has the details (500). |
| `not_implemented` | Not supported on this node (501). |
| `peer_error` | Another member of the cluster did not answer or failed (502). |
| `unavailable` | The node cannot do this right now (503). |
| `timeout` | Something the request waited for did not answer in time (504). |
| `api_gone` | An address of the API before 1.0: the detail names the one to use (410). |
| `changed_elsewhere` | Another node of the cluster changed the same thing first: nothing was applied (409). |
| `not_confirmed` | The cluster did not take the change in time, or refused it: it is copied when it answers (503). |
| `read_only` | This node changes nothing right now (no quorum, a cluster update): the detail says why (503). |
| `cluster_unavailable` | The cluster's configuration service does not answer: nothing new can be created (503). |

## Clusters: `?node=`

A VM, a template, a network, a storage pool and an ISO image live on one node. Every route under `/vms`, `/templates`,
`/networks`, `/storage` and `/isos`, and the backups, the backup schedule and the export of a VM, takes `?node=`: when it
names another member of the cluster, the request goes to that member, without the parameter, in the same user's name,
and its answer comes back as it is. That member does the work, checks the rights and records the task and the audit
entry, as Proxmox VE passes a request to the node that owns the guest; follow its task with
`GET /tasks/{id}?node=<member>` (`?on=` is the same). Absent, `local` or this node's name: here. A node registered over
SSH only (no Hyperlite on it) is reached by the routes that took `node` before (libvirt over SSH); any other route
refuses it (409), as it refuses a name that is no node, rather than act here on an object of the same name. A console,
terminal, serial, SPICE or tunnel ticket stays on the node that answers, where its WebSocket opens. An ISO upload
(`POST /isos?node=<member>`) goes to that member too, as Proxmox VE relays an upload to the node that stores it: the
file is read from this node's spool while it is sent, over the members' HTTPS only (502 when that way is closed). A
member that does not answer, or refuses the session, reads as 502 naming it, with the reason; a change whose answer
was lost says it may have been made, and is never sent twice.

## Long operations

Operations that can take time (creation, snapshots, backups, migration, updates) return a task id; poll `GET /tasks/{id}` for progress. VM creation (`POST /vms`), clone (`POST /vms/{name}/clone`), template deployment (`POST /templates/{name}/deploy`) and template creation (`POST /templates/from-vm/{name}`) hold the request until done by default, as before; with `?wait=false` they make every check first (a refusal is the usual 4xx), then answer `202 {"task_id", "status": "running", "name"}` at once, `name` being the object the task makes. A client that copies large disks uses it: a proxy cuts a request held for the whole copy. `GET /tasks/{id}/log` returns the task's own log, line by line. `POST /tasks/{id}/cancel` asks a running task to stop (`{"result": "requested"}`), or closes one that no process runs any more (`"abandoned"`); a task that cannot stop midway answers 409, and an administrator may close its record anyway with `?force=true`. In the task list, `clean_shutdown`, `orphaned` and `cancellable` say which applies; `GET /tasks?object=<name>&family=vm|container` is one object's history.

## Permissions

Each endpoint requires a global role or a scoped privilege (for example `vm.hardware`, `vm.console`, `vm.clone`). See `app/core/permissions.py` for the catalogue. An assignment (`POST /acl`) is given on a VM, a pool of VMs, a container, a storage pool (`storage`, by name) or a network (`network`, by name).

Creating a VM is open to an account that is not an administrator, step by step as vSphere and Proxmox VE allow it: `vm.allocate` on the pool the VM goes in (`resource_pool` in `POST /vms`, the pool's id, which such an account must give; the VM becomes a member of it), `storage.allocate` on the storage pool of its disks (`storage_pool`, `default` when absent) and `network.assign` on its network. `vm.allocate` on a VM, or on a pool holding it, also allows deleting it. The same rights apply to an existing VM, as Proxmox VE checks them there too: attaching or growing a disk needs `storage.allocate` on its storage pool, attaching an interface or changing its network `network.assign` on that network, and a clone (whose disks are copied into the `default` storage pool) `storage.allocate` on `default`. Creating a volume (`POST /storage/{pool}/volumes`, a disk to attach) needs `storage.allocate` on that pool; deleting one stays an administrator's. An assignment on a storage pool or a network is refused (409) while a member of the cluster runs a version that does not know them. `vm.options` and `container.options` (part of the Manager role) cover start at boot, notes and tags, and a cloud image VM's account.

## Object settings

| Endpoint | What it does |
|---|---|
| `GET`/`PUT /vms/{name}/boot?node=` | Start at boot: `autostart`, `position` (sequence position, none last), `delay_s` (pause before the next VM). Run once per boot of the VM's node. |
| `GET`/`PUT /vms/{name}/cloud-init` | A cloud image VM's account: `user`, `ssh_keys`, `password` (write-only, never returned); and its customisation: `hostname` (the VM's name when empty), `domain`, `network` (static addresses by interface: `mac`, the first interface when absent, `addresses` with their prefix, `gateway`; the interfaces not listed keep DHCP), `dns`, `dns_search`, `user_data` (a cloud-config added to Hyperlite's own; the keys of the account and the host name are refused). A field left out of `PUT` keeps its value; an empty one clears it. Applied at the VM's next boot. `POST /vms` takes `ssh_keys` and `customize` (the same fields, `network` without `mac`) for a VM made from the cloud image, written before its first boot. |
| `GET /vms/{name}/hardware-options` | Disk options, boot order, ballooning and machine type (`disks`, `interfaces`, `boot_order`, `ballooning`, `machine`, `newer_machines`, `running`). |
| `PUT /vms/{name}/disks/{dev}/options` | `cache`, `discard`, `io`, `iothread` (virtio only), `iops`, `mbps` (limits apply live). Returns `needs_restart` when the rest waits for the next start. |
| `PUT /vms/{name}/network?mac=` | Move an interface to another network (`network`, `vlan_tag`) in place, keeping its MAC address; `mac` names it, the first interface when absent. |
| `PUT /vms/{name}/boot-order` | `position`: disk targets and `net:<mac>` in boot order. |
| `PUT /vms/{name}/balloon` | `active`, `minimum_mb` (privilege `vm.resize`). |
| `PUT /vms/{name}/machine` | `machine`: the current version of the same family, on a stopped VM. |
| `GET /acl/object/{vm\|container}/{name}` | Assignments on one VM or container (admin), with those a VM inherits from its pools (`inherited_from`: the pool name, `null` for its own). |
| `GET /vms/{name}/pending-changes` | What a running VM takes only at its next start: `running`, `changes` (`key`, `object`, `current`, `next`; `null` for a device on one side only). |
| `GET /host/certificate` | This node's HTTPS certificate (admin): `subject`, `names`, `issuer`, `end`, `days_left`, `self_signed`, `source` (`auto`, `import`, `acme:<domain>`), `certbot`, `previous`. |
| `POST /host/certificate`, `POST /host/certificate/acme`, `/previous`, `/self-signed` | Replace it: a PEM pair (`certificate`, `key`, optional `chain`), Let's Encrypt by HTTP-01 through certbot (`domain`, `email`, `test`), the previous pair, or a new self-signed one. The pair is checked before use; the service restarts a few seconds after the answer. |
| `GET /host/system/updates?refresh=`, `POST /host/system/updates/upgrade` | This node's upgradable packages (`packages`: `name`, `version`, `since`, `security`), `restart_required`; the upgrade (`packages`) runs as a task and never includes `hyperlite`. Admin. |
| `GET`/`POST /metric-servers`, `PUT`/`DELETE /metric-servers/{id}`, `POST /metric-servers/{id}/test` | External metric servers (admin): `type` `influxdb` (`url`, `org`, `bucket`, write-only `token`) or `graphite` (`host`, `port`, `prefix`), `active`; each collected sample is pushed to the active ones, `last_sent_at` and `last_error` tell how it went. |
| `GET`/`POST /backup-groups`, `PUT`/`DELETE /backup-groups/{id}`, `POST /backup-groups/{id}/run` | Grouped backup jobs (admin): `selection` (`all`, `tag`, `pool`) and `value`, `excluded`, `frequency`, `time`, `target_dir`, retention (`retention_count`, `keep_days`, `keep_weeks`, `keep_months`), `active`; the list adds the VMs selected now (`vms`). |
| `PUT /vms/{name}/backup-schedule` | Also takes `keep_days`, `keep_weeks`, `keep_months` (GFS retention); `target_dir` must be absolute and outside the system's directories. |
| `GET`/`POST /backup-stores`, `GET`/`DELETE /backup-stores/{name}`, `POST /backup-stores/{name}/gc` | Backup stores (`docs/design/backup-store.md`), as Proxmox Backup Server's datastores: a store is made (admin) in a directory of a storage pool of files (`pool`, `directory`, default `hyperlite-store`), or takes over the store already there; each entry says whether this node reaches it (`available`, `problem`), its space and its backups, and its last garbage collection (`last_gc`, weekly, or on demand: a task). `encrypted: true` makes an encrypted store, and `master_key` (`none`, `generate`, `own` with `master_public_key`) a master recovery key: the creation's answer alone holds the store key's code (`key_code`, `key_qr_svg`) and a generated master key's private half (`master_private_key`), never shown again; `key` takes over an encrypted store already in the directory, and `copy_of` makes a new store as the copy of another (its chunk size and key). `GET`/`POST /backup-store-syncs`, `GET`/`PUT`/`DELETE /backup-store-syncs/{id}`, `POST /backup-store-syncs/{id}/run` (admin): copies of a store into its copy store, pulled on their own schedule (`frequency`, `time`) with a retention of their own (`retention_count`, `keep_days`, `keep_weeks`, `keep_months`): a deletion at the source never reaches the copy; `last_result` says what the last run did. `GET /backups/site-recovery/scan?path=` also reads a backup store of this cluster (a copy at the other site), and the recovery restores from it. **Another cluster's stores**: `GET`/`POST /backup-remotes`, `DELETE /backup-remotes/{name}`, `GET /backup-remotes/{name}/stores`, `POST /backup-remotes/probe` (the certificate fingerprint an address presents), `GET /backup-remotes/this-node` (this node's own, to give the other side): a remote is an address, its pinned certificate and an API token of a dedicated account there holding only `backup_store.read` on its stores (the role `store_reader`, assigned on a `backup_store`); `copy_of: "<remote>:<store>"` makes a copy of one with no key (`locked` while encrypted), filled by a copy job whose `source` is `<remote>:<store>`, and `POST /backup-stores/{name}/unlock` gives it its key the day it is restored from. The source serves its stores read-only to such an account: `GET /backup-stores/{name}/export`, `.../export/backups/{vm}/{time}/{file}`, `.../export/chunks/{digest}`, as they are on disk. Removing one keeps its files on the pool and is refused while a job uses it. `store` on `POST /vms/{name}/backups`, `PUT /vms/{name}/backup-schedule` and the grouped jobs backs up into it instead of a directory: every disk type (qcow2, raw, ZFS, LVM, iSCSI), running or stopped, only the chunks the store lacks written; a backup's entry names its `store`. |
| `POST /backups/{id}/files`, `GET /file-restore/{session}/ls?device=&path=`, `GET /file-restore/{session}/download?device=&path=`, `DELETE /file-restore/{session}`, `GET /file-restore/status` | File-level restore (admin): a read-only browsing session on a finished backup's disks (libguestfs), its filesystems and folders, a file or a folder (`.tar.gz`) downloaded. Sessions close after 10 minutes without a request. |
| `GET`/`PUT /ldap/config`, `POST /ldap/test` | LDAP / Active Directory sign-in (admin): `url`, `starttls`, `verify_tls`, `ca_cert`, `bind_dn`, write-only `bind_password`, `base_dn`, `user_filter` (with `{username}`), `group_attribute`, `admin_groups`, `allowed_groups`, `enabled`. The test binds with the service account and, given `username` and `password`, says whether they are accepted and with which role. Directory accounts sign in through `POST /auth/login` (`auth_source`: `ldap`). |
| `POST /host/system/power` | Reboot or power off this node (admin): `action` (`reboot`, `poweroff`), `confirmation` (the host name), `stop_guests` (shut running VMs and containers down first; without it they are a 409). Runs as a task; the node goes down only when every guest stopped. |
| `GET`/`PUT /host/system/dns`, `/time` (+ `GET /time/zones`), `/syslog` | DNS servers and search domains (`managed_by`, `modifiable`), time zone, NTP and NTP servers, remote syslog (`host`, `port`, `protocol`). Admin. |
| `PATCH /networks/{name}` | A NAT or isolated network's `mode` (`nat`, `isolated`), `address` (gateway), `netmask` and `dhcp` (`{start, end}` or `null`). A DHCP range change is live; the rest waits for the network's restart (`needs_restart`). `GET /networks/{name}` returns `ipam` and `needs_restart`. |
| `POST /networks/{name}/reservations`, `DELETE /networks/{name}/reservations/{mac}` | DHCP reservations: `mac`, `ip`, optional `name`; applied live and in the network's definition. |
| `GET /meta?kind=`, `GET`/`PUT /meta/{vm,container,node}/{name}?node=` | Notes (plain text) and tags of an object. The listing gives tags and `has_notes`, not the notes. |
| `GET /cfs/shadow`, `POST /cfs/shadow/seed`, `POST /cfs/shadow/enable`, `POST /cfs/shadow/disable` | Shadow mode of `hyperlite-cfs` (admin): `active`, `force` (set by `HYPERLITE_CFS_SHADOW=1`), `installed` (the daemon is on this node), `reachable`, `copies`, `failures`, `last_error`, `daemon` (`mode`, `quorum`, `version`, `entries`), per domain `entries`, `missing`, `extra`, `different` and `examples`, and the total `differences`. The seed makes the daemon hold what SQLite holds (`written`, `deleted`); 409 while shadow mode is off, 503 when the daemon does not answer. `activer` starts `hyperlite-cfs.service`, turns shadow mode on and seeds (503 with the reason when it cannot); `desactiver` turns it off and stops the service (409 while `HYPERLITE_CFS_SHADOW=1` forces it). |
| `GET /cluster`, `POST /cluster/create`, `POST /cluster/adhesion`, `POST /cluster/join`, `POST /cluster/members/{name}/remove` | The cluster (admin, `app/core/cluster_setup.py`): `corosync_installed`, `in_cluster`, `name`, `quorum`, `members` (`name`, `nodeid`, `address`), `node`. `creer` (`name`, `address`) makes this node the first member; `adhesion` returns the join information (`information`, `cluster`, `expire`), a secret valid 30 minutes for one node; `rejoindre` (`information`, `address`, `confirmation` = the cluster's name) makes this node a member, replaces its configuration and keys by the cluster's, and restarts Hyperlite (`restart`); `retirer` (`confirmation` = the member's name) removes a member that is off. 409 with the reason, 503 when hyperlite-cfs does not answer. |
| `POST /cluster/members` | Called by a joining node, without a session: `ticket` (from the join information), `name`, `address`, `ssh_key`. Returns the cluster's configuration, Corosync's key (`corosync_key`), the keys of `.env` and the members' SSH keys; 403 for a ticket that is wrong, used or expired. |
| `GET`/`PATCH /containers/{name}` | A container's details (`interfaces`, `dns`, `autostart`) and changing `vcpu`, `memory_mb`, `dns`, `autostart`; `needs_restart` says when part of it applies at the next start. |
