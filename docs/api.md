# API conventions

The HTTP API is what the dashboard uses; an interactive OpenAPI description (`/docs`, `/redoc`, `/openapi.json`) is served by the running service when `HYPERLITE_API_DOCS=1` is set (off by default: it answers without authentication). `GET /health` answers everyone with `{"status", "environment"}`; the host name and software versions are only reported to a signed-in caller. Requests are authenticated with `Authorization: Bearer <token>`, where the token is a session JWT (from `POST /auth/login`, or the two-step `POST /auth/login/2fa`) or a personal API token (`hlt_...`).

## French wire format

For historical reasons the API and the database use French identifiers and values. They are a compatibility contract with the dashboard and with existing installations, so they are **not** renamed in the English code base; only messages, labels and comments are English. Examples:

| Kind | Examples |
|---|---|
| Field names | `name` (name), `state` (state), `memory_mb` (memory in MB), `stockage_total_go`, `created_at`, `status`, `target` |
| VM states | `active`, `stopped`, `suspended`, `crashed` |
| Task and backup status | `running`, `succeeded`, `failed`, `success` |
| Global roles | `admin`, `viewer` |
| Backup frequency and mode | `daily`, `weekly`, `monthly`; `live`, `cold` |

The dashboard translates these values for display (`dashboard/src/lib/labels.js`, `StatusBadge`). A future API version could introduce English identifiers; until then, treat the French values as stable.

## Errors

Errors are returned as JSON `{"detail": "..."}` with an appropriate HTTP status. Messages are in English and, for infrastructure failures, categorized into an actionable cause (see `app/core/error_messages.py`).

## Long operations

Operations that can take time (creation, snapshots, backups, migration, updates) return a task id; poll `GET /tasks/{id}` for progress. `GET /tasks/{id}/log` returns the task's own log, line by line. `POST /tasks/{id}/cancel` asks a running task to stop (`{"resultat": "requested"}`), or closes one that no process runs any more (`"abandoned"`); a task that cannot stop midway answers 409, and an administrator may close its record anyway with `?force=true`. In the task list, `clean_shutdown`, `orphaned` and `cancellable` say which applies; `GET /tasks?objet=<name>&famille=vm|container` is one object's history.

## Permissions

Each endpoint requires a global role or a scoped privilege (for example `vm.hardware`, `vm.console`, `vm.clone`). See `app/core/permissions.py` for the catalogue. `vm.options` and `container.options` (part of the Manager role) cover start at boot, notes and tags, and a cloud image VM's account.

## Object settings

| Endpoint | What it does |
|---|---|
| `GET`/`PUT /vms/{name}/boot?node=` | Start at boot: `autostart`, `position` (sequence position, none last), `delay_s` (pause before the next VM). Run once per boot of the VM's node. |
| `GET`/`PUT /vms/{name}/cloud-init` | A cloud image VM's account: `user`, `ssh_keys`, `password` (write-only, never returned). Applied at the VM's next boot. |
| `GET /vms/{name}/hardware-options` | Disk options, boot order, ballooning and machine type (`disks`, `interfaces`, `boot_order`, `ballooning`, `machine`, `newer_machines`, `running`). |
| `PUT /vms/{name}/disks/{dev}/options` | `cache`, `discard`, `io`, `iothread` (virtio only), `iops`, `mbps` (limits apply live). Returns `needs_restart` when the rest waits for the next start. |
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
| `POST /backups/{id}/files`, `GET /file-restore/{session}/ls?device=&path=`, `GET /file-restore/{session}/download?device=&path=`, `DELETE /file-restore/{session}`, `GET /file-restore/status` | File-level restore (admin): a read-only browsing session on a finished backup's disks (libguestfs), its filesystems and folders, a file or a folder (`.tar.gz`) downloaded. Sessions close after 10 minutes without a request. |
| `GET`/`PUT /ldap/config`, `POST /ldap/test` | LDAP / Active Directory sign-in (admin): `url`, `starttls`, `verify_tls`, `ca_cert`, `bind_dn`, write-only `bind_password`, `base_dn`, `user_filter` (with `{username}`), `group_attribute`, `admin_groups`, `allowed_groups`, `enabled`. The test binds with the service account and, given `username` and `password`, says whether they are accepted and with which role. Directory accounts sign in through `POST /auth/login` (`auth_source`: `ldap`). |
| `POST /host/system/power` | Reboot or power off this node (admin): `action` (`reboot`, `poweroff`), `confirmation` (the host name), `stop_guests` (shut running VMs and containers down first; without it they are a 409). Runs as a task; the node goes down only when every guest stopped. |
| `GET`/`PUT /host/system/dns`, `/time` (+ `GET /time/zones`), `/syslog` | DNS servers and search domains (`managed_by`, `modifiable`), time zone, NTP and NTP servers, remote syslog (`host`, `port`, `protocol`). Admin. |
| `PATCH /networks/{name}` | A NAT or isolated network's `mode` (`nat`, `isolated`), `address` (gateway), `netmask` and `dhcp` (`{debut, fin}` or `null`). A DHCP range change is live; the rest waits for the network's restart (`needs_restart`). `GET /networks/{name}` returns `ipam` and `needs_restart`. |
| `POST /networks/{name}/reservations`, `DELETE /networks/{name}/reservations/{mac}` | DHCP reservations: `mac`, `ip`, optional `name`; applied live and in the network's definition. |
| `GET /meta?kind=`, `GET`/`PUT /meta/{vm,container,node}/{name}?node=` | Notes (plain text) and tags of an object. The listing gives tags and `has_notes`, not the notes. |
| `GET /cfs/shadow`, `POST /cfs/shadow/seed`, `POST /cfs/shadow/activer`, `POST /cfs/shadow/desactiver` | Shadow mode of `hyperlite-cfs` (admin): `active`, `force` (set by `HYPERLITE_CFS_SHADOW=1`), `installed` (the daemon is on this node), `reachable`, `copies`, `failures`, `last_error`, `daemon` (`mode`, `quorum`, `version`, `entries`), per domain `entries`, `missing`, `extra`, `different` and `examples`, and the total `differences`. The seed makes the daemon hold what SQLite holds (`written`, `deleted`); 409 while shadow mode is off, 503 when the daemon does not answer. `activer` starts `hyperlite-cfs.service`, turns shadow mode on and seeds (503 with the reason when it cannot); `desactiver` turns it off and stops the service (409 while `HYPERLITE_CFS_SHADOW=1` forces it). |
| `GET /cluster`, `POST /cluster/creer`, `POST /cluster/adhesion`, `POST /cluster/rejoindre`, `POST /cluster/membres/{nom}/retirer` | The cluster (admin, `app/core/cluster_setup.py`): `corosync_installed`, `in_cluster`, `name`, `quorum`, `members` (`name`, `nodeid`, `address`), `node`. `creer` (`name`, `address`) makes this node the first member; `adhesion` returns the join information (`information`, `cluster`, `expire`), a secret valid 30 minutes for one node; `rejoindre` (`information`, `address`, `confirmation` = the cluster's name) makes this node a member, replaces its configuration and keys by the cluster's, and restarts Hyperlite (`restart`); `retirer` (`confirmation` = the member's name) removes a member that is off. 409 with the reason, 503 when hyperlite-cfs does not answer. |
| `POST /cluster/membres` | Called by a joining node, without a session: `ticket` (from the join information), `name`, `address`, `ssh_key`. Returns the cluster's configuration, Corosync's key (`corosync_key`), the keys of `.env` and the members' SSH keys; 403 for a ticket that is wrong, used or expired. |
| `GET`/`PATCH /containers/{name}` | A container's details (`interfaces`, `dns`, `autostart`) and changing `vcpu`, `memory_mb`, `dns`, `autostart`; `needs_restart` says when part of it applies at the next start. |
