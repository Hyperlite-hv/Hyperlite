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
| `PATCH /networks/{name}` | A NAT or isolated network's `mode` (`nat`, `isole`), `adresse` (gateway), `masque` and `dhcp` (`{debut, fin}` or `null`). A DHCP range change is live; the rest waits for the network's restart (`a_redemarrer`). `GET /networks/{name}` returns `ipam` and `a_redemarrer`. |
| `POST /networks/{name}/reservations`, `DELETE /networks/{name}/reservations/{mac}` | DHCP reservations: `mac`, `ip`, optional `nom`; applied live and in the network's definition. |
| `GET /meta?kind=`, `GET`/`PUT /meta/{vm,container,node}/{name}?node=` | Notes (plain text) and tags of an object. The listing gives tags and `a_des_notes`, not the notes. |
| `GET`/`PATCH /containers/{name}` | A container's details (`interfaces`, `dns`, `demarrage_auto`) and changing `vcpu`, `memory_mb`, `dns`, `demarrage_auto`; `a_redemarrer` says when part of it applies at the next start. |
