# API conventions

The HTTP API is what the dashboard uses; an interactive OpenAPI description is served by the running service at `/docs`. Requests are authenticated with `Authorization: Bearer <token>`, where the token is a session JWT (from `POST /auth/login`, or the two-step `POST /auth/login/2fa`) or a personal API token (`hlt_...`).

## French wire format

For historical reasons the API and the database use French identifiers and values. They are a compatibility contract with the dashboard and with existing installations, so they are **not** renamed in the English code base; only messages, labels and comments are English. Examples:

| Kind | Examples |
|---|---|
| Field names | `nom` (name), `etat` (state), `memoire_mo` (memory in MB), `stockage_total_go`, `cree_le`, `statut`, `cible` |
| VM states | `actif`, `arrete`, `suspendu`, `plante` |
| Task and backup status | `en_cours`, `termine`, `echec`, `succes` |
| Global roles | `admin`, `observateur` |
| Backup frequency and mode | `quotidien`, `hebdomadaire`, `mensuel`; `chaud`, `froid` |
| Deployment profiles and allocation policies | `homelab`, `standard`, `avance`; `limites`, `surallocation`, `libre` |

The dashboard translates these values for display (`dashboard/src/lib/labels.js`, `StatusBadge`). A future API version could introduce English identifiers; until then, treat the French values as stable.

## Errors

Errors are returned as JSON `{"detail": "..."}` with an appropriate HTTP status. Messages are in English and, for infrastructure failures, categorized into an actionable cause (see `app/core/error_messages.py`).

## Long operations

Operations that can take time (creation, snapshots, backups, migration, updates) return a task id; poll `GET /tasks/{id}` for progress.

## Permissions

Each endpoint requires a global role or a scoped privilege (for example `vm.hardware`, `vm.console`, `vm.clone`). See `app/core/permissions.py` for the catalogue.
