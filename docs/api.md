# API conventions

The HTTP API is what the dashboard uses; an interactive OpenAPI description (`/docs`, `/redoc`, `/openapi.json`) is served by the running service when `HYPERLITE_API_DOCS=1` is set (off by default: it answers without authentication). `GET /health` answers everyone with `{"status", "environment"}`; the host name and software versions are only reported to a signed-in caller. Requests are authenticated with `Authorization: Bearer <token>`, where the token is a session JWT (from `POST /auth/login`, or the two-step `POST /auth/login/2fa`) or a personal API token (`hlt_...`).

## French wire format

For historical reasons the API and the database use French identifiers and values. They are a compatibility contract with the dashboard and with existing installations, so they are **not** renamed in the English code base; only messages, labels and comments are English. Examples:

| Kind | Examples |
|---|---|
| Field names | `nom` (name), `etat` (state), `memoire_mo` (memory in MB), `stockage_total_go`, `cree_le`, `statut`, `cible` |
| VM states | `actif`, `arrete`, `suspendu`, `plante` |
| Task and backup status | `en_cours`, `termine`, `echec`, `succes` |
| Global roles | `admin`, `observateur` |
| Backup frequency and mode | `quotidien`, `hebdomadaire`, `mensuel`; `chaud`, `froid` |

The dashboard translates these values for display (`dashboard/src/lib/labels.js`, `StatusBadge`). A future API version could introduce English identifiers; until then, treat the French values as stable.

## Errors

Errors are returned as JSON `{"detail": "..."}` with an appropriate HTTP status. Messages are in English and, for infrastructure failures, categorized into an actionable cause (see `app/core/error_messages.py`).

## Long operations

Operations that can take time (creation, snapshots, backups, migration, updates) return a task id; poll `GET /tasks/{id}` for progress. `GET /tasks/{id}/log` returns the task's own log, line by line. `POST /tasks/{id}/cancel` asks a running task to stop (`{"resultat": "requested"}`), or closes one that no process runs any more (`"abandoned"`); a task that cannot stop midway answers 409, and an administrator may close its record anyway with `?force=true`. In the task list, `arret_propre`, `orpheline` and `annulable` say which applies; `GET /tasks?objet=<name>&famille=vm|container` is one object's history.

## Permissions

Each endpoint requires a global role or a scoped privilege (for example `vm.hardware`, `vm.console`, `vm.clone`). See `app/core/permissions.py` for the catalogue. `vm.options` and `container.options` (part of the Manager role) cover start at boot, notes and tags, and a cloud image VM's account.

## Object settings

| Endpoint | What it does |
|---|---|
| `GET`/`PUT /vms/{name}/boot?node=` | Start at boot: `demarrage_auto`, `ordre` (sequence position, none last), `delai_s` (pause before the next VM). Run once per boot of the VM's node. |
| `GET`/`PUT /vms/{name}/cloud-init` | A cloud image VM's account: `utilisateur`, `cles_ssh`, `mot_de_passe` (write-only, never returned). Applied at the VM's next boot. |
| `GET /vms/{name}/hardware-options` | Disk options, boot order, ballooning and machine type (`disques`, `interfaces`, `ordre_demarrage`, `ballooning`, `machine`, `machines_plus_recentes`, `en_marche`). |
| `PUT /vms/{name}/disks/{dev}/options` | `cache`, `discard`, `io`, `iothread` (virtio only), `iops`, `mbps` (limits apply live). Returns `a_redemarrer` when the rest waits for the next start. |
| `PUT /vms/{name}/boot-order` | `ordre`: disk targets and `net:<mac>` in boot order. |
| `PUT /vms/{name}/balloon` | `actif`, `minimum_mo` (privilege `vm.resize`). |
| `PUT /vms/{name}/machine` | `machine`: the current version of the same family, on a stopped VM. |
| `GET /acl/object/{vm\|container}/{name}` | Assignments on one VM or container (admin), with those a VM inherits from its pools (`herite_de`: the pool name, `null` for its own). |
| `GET /vms/{name}/pending-changes` | What a running VM takes only at its next start: `en_marche`, `changements` (`cle`, `objet`, `actuel`, `prochain`; `null` for a device on one side only). |
| `GET /host/certificate` | This node's HTTPS certificate (admin): `sujet`, `noms`, `emetteur`, `fin`, `jours_restants`, `auto_signe`, `source` (`auto`, `import`, `acme:<domain>`), `certbot`, `precedent`. |
| `POST /host/certificate`, `POST /host/certificate/acme`, `/previous`, `/self-signed` | Replace it: a PEM pair (`certificat`, `cle`, optional `chaine`), Let's Encrypt by HTTP-01 through certbot (`domaine`, `email`, `test`), the previous pair, or a new self-signed one. The pair is checked before use; the service restarts a few seconds after the answer. |
| `GET /host/system/updates?refresh=`, `POST /host/system/updates/upgrade` | This node's upgradable packages (`paquets`: `nom`, `version`, `depuis`, `securite`), `redemarrage_requis`; the upgrade (`paquets`) runs as a task and never includes `hyperlite`. Admin. |
| `GET`/`POST /metric-servers`, `PUT`/`DELETE /metric-servers/{id}`, `POST /metric-servers/{id}/test` | External metric servers (admin): `type` `influxdb` (`url`, `org`, `bucket`, write-only `jeton`) or `graphite` (`hote`, `port`, `prefixe`), `actif`; each collected sample is pushed to the active ones, `dernier_envoi` and `derniere_erreur` tell how it went. |
| `GET`/`POST /backup-groups`, `PUT`/`DELETE /backup-groups/{id}`, `POST /backup-groups/{id}/run` | Grouped backup jobs (admin): `selection` (`toutes`, `etiquette`, `pool`) and `valeur`, `exclues`, `frequence`, `heure`, `cible_dir`, retention (`retention_count`, `garder_jours`, `garder_semaines`, `garder_mois`), `actif`; the list adds the VMs selected now (`vms`). |
| `PUT /vms/{name}/backup-schedule` | Also takes `garder_jours`, `garder_semaines`, `garder_mois` (GFS retention); `cible_dir` must be absolute and outside the system's directories. |
| `POST /backups/{id}/files`, `GET /file-restore/{session}/ls?device=&path=`, `GET /file-restore/{session}/download?device=&path=`, `DELETE /file-restore/{session}`, `GET /file-restore/status` | File-level restore (admin): a read-only browsing session on a finished backup's disks (libguestfs), its filesystems and folders, a file or a folder (`.tar.gz`) downloaded. Sessions close after 10 minutes without a request. |
| `POST /host/system/power` | Reboot or power off this node (admin): `action` (`reboot`, `poweroff`), `confirmation` (the host name), `arreter_invites` (shut running VMs and containers down first; without it they are a 409). Runs as a task; the node goes down only when every guest stopped. |
| `GET`/`PUT /host/system/dns`, `/time` (+ `GET /time/zones`), `/syslog` | DNS servers and search domains (`gere_par`, `modifiable`), time zone, NTP and NTP servers, remote syslog (`hote`, `port`, `protocole`). Admin. |
| `PATCH /networks/{name}` | A NAT or isolated network's `mode` (`nat`, `isole`), `adresse` (gateway), `masque` and `dhcp` (`{debut, fin}` or `null`). A DHCP range change is live; the rest waits for the network's restart (`a_redemarrer`). `GET /networks/{name}` returns `ipam` and `a_redemarrer`. |
| `POST /networks/{name}/reservations`, `DELETE /networks/{name}/reservations/{mac}` | DHCP reservations: `mac`, `ip`, optional `nom`; applied live and in the network's definition. |
| `GET /meta?kind=`, `GET`/`PUT /meta/{vm,container,node}/{name}?node=` | Notes (plain text) and tags of an object. The listing gives tags and `a_des_notes`, not the notes. |
| `GET`/`PATCH /containers/{name}` | A container's details (`interfaces`, `dns`, `demarrage_auto`) and changing `vcpu`, `memory_mb`, `dns`, `demarrage_auto`; `a_redemarrer` says when part of it applies at the next start. |
