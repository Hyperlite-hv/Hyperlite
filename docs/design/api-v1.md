# API v1: one English contract, before 1.0

Status: **design, for review**. No code before Antho's approval (code review #448).

## Why

A company that adopts Hyperlite automates it: scripts, Terraform, monitoring. Today the HTTP API has no version, and
its contract mixes English and French: keys such as `statut`, `cible`, `noeud`, `cree_le`, values such as `echec`
(295 occurrences in the routers), `en_cours`, `arrete`. Every rename would break a client's integration, and a team
taking over the code has to learn a private vocabulary. 1.0 is the last moment to fix it for free: there is no client
yet.

## Decisions (Antho, 2026-10-17)

| Question | Decision |
| --- | --- |
| Where | Every route under `/api/v1`. |
| Old routes | **Removed at 1.0**, no deprecation period. |
| Language | English for **keys and values**. The dashboard translates values for display, as it already does. |
| Database | Renamed **in the same change** (tables, columns, stored values). |
| Cluster update | **The whole cluster at once** for this version: the shared configuration is read-only meanwhile, VMs keep running. No bridge version. |
| Existing backups | **Readable for ever** in their current format, never rewritten. New backups are written in English. |
| Old `hyperlite-cli` | A clear "update hyperlite-cli" message, never a list of 404s. |

## Inventory (branch `test`, 17/10)

- **Routes**: 325 HTTP (127 GET, 117 POST, 31 PUT, 10 PATCH, 40 DELETE) and 6 WebSocket. French path words:
  `creer`, `activer`, `desactiver`, `rejoindre`, `retirer`, `membres`, `adhesion`, `acces`, `principal`.
- **Database**: 54 tables, 221 columns, about 100 of them French (`statut`, `cible`, `erreur`, `nom`, `noeuds`, `etat_ha`,
  `cree_le`, `debut_le`, `fin_le`, `progres`, `valeur`, `cle`, `garder_jours`, `prochaine_execution`, `tls_non_verifie`…).
- **Stored values**: task states, audit results (`succes`/`echec`), VM, pool and node states, backup states and
  verification results, schedules (`quotidien`…), selections (`toutes`, `etiquette`).
- **Replicated configuration**: the cfs tree is keyed by table and column names (`app/repositories/cfs/tables.py`).
- **Files on disk**: backup manifests (`fichiers`, `role: disque`, `cible`), `vm-config.json` in backups, VM exports.
- **Clients of the API**: the dashboard (`dashboard/src/api/client.js`), the CLI (`cli/`, Go), node-to-node calls
  (`rolling_update.peer_api`, `cluster_view.forward_to`), the e2e tests, the docs (`docs/api.md`).

## The contract

### Naming

- `snake_case` keys, English, no abbreviations a reader must guess (`size_bytes`, not `taille_octets`; `memory_mb`).
- Times: `*_at` (ISO 8601 UTC); durations `*_s`; sizes `*_bytes` or `*_mb` / `*_gb` when the unit is the user's.
- Values: lowercase English words, `snake_case` for two words.

### Glossary (excerpt; the full table is generated from the code before the first code PR)

| Today | v1 | Today | v1 |
| --- | --- | --- | --- |
| `statut` | `status` | `en_cours` | `running` |
| `cible` | `target` | `en_attente` | `pending` |
| `erreur` | `error` | `termine` | `succeeded` |
| `nom` | `name` | `echec` | `failed` |
| `noeud` / `noeuds` | `node` / `nodes` | `abandonne` | `cancelled` |
| `etat` | `state` | `succes` (audit) | `success` |
| `cree_le` | `created_at` | `actif` (VM) | `running` |
| `debut_le` / `fin_le` | `started_at` / `finished_at` | `arrete` | `stopped` |
| `progres` | `progress` | `en_arret` | `shutting_down` |
| `valeur` / `cle` | `value` / `key` | `plante` | `crashed` |
| `chemin` | `path` | `degrade` (pool) | `degraded` |
| `garder_jours` | `keep_days` | `quotidien` | `daily` |

### Shape

- Errors keep FastAPI's `{"detail": ...}`; v1 adds a stable `code` (`{"detail": "...", "code": "vm_running"}`) so a
  script tests a code, never an English sentence (the same rule as #449 for libvirt).
- Lists answer `{"items": [...], "total": n}` everywhere (today some answer a bare list).
- Long operations answer `202` with `{"task_id": ...}` everywhere.
- The OpenAPI description is the contract: published with each release, and a CI check fails a PR that changes v1 in
  a way that is not additive.

### Versioning rule after 1.0

Inside v1, only additive changes (a new route, a new optional field, a new value documented as possible). Anything
else waits for v2, which would live next to v1 for at least one major version.

## The database

One migration, run by each node at the start of the new version, in one SQLite transaction, after a copy of the
database file (`data/hyperlite.db.before-v1`). Tables are rebuilt with English names (SQLite `ALTER TABLE … RENAME
COLUMN` for columns, `UPDATE` for stored values). The migration is idempotent: a node that crashed in the middle starts
it again from the copy.

The cfs tree is rewritten once, by the leader, when every member runs the new version (see the cluster update below).

## The cluster update (whole cluster at once)

The rolling update (`app/core/rolling_update.py`) gets a mode for a version marked `breaking_cluster` in its release
metadata:

1. Plan, as today, plus a warning: the shared configuration is read-only until every node is updated.
2. The leader sets the cluster read-only (`cluster_lead`, the reason shown on every page).
3. Every node is updated, the leader last. No VM moves: Hyperlite's update does not stop the VMs.
4. Each node migrates its own database; the leader then rewrites the cfs tree and lifts the read-only state.
5. A node that fails stays on the old version; the cluster stays read-only and the dashboard says which node and why.
   Going back means the rollback of #395 on the nodes already updated, from the database copy of step 4.

A single node is the same without steps 2 and 5.

## Files on disk

- Backups: a reader for both manifests (French and English) and both `vm-config.json`. New backups are written in
  English. Old backups are never rewritten (a NAS, a disk taken away).
- Exports: same rule.

## Old clients

- Old routes return `410 Gone` with `{"detail": "This API is gone since Hyperlite 1.0: use /api/v1", "code":
  "api_gone"}`. For a request with `User-Agent: hyperlite-cli/<old>`, the detail says "Update hyperlite-cli".
- The new CLI ships in the same release and checks the server's API version at sign-in.

## Order of work (each step a PR, CI green, lab-tested)

1. Glossary generated from the code, reviewed by Antho.
2. Database migration and cfs tree rewrite, with the lab cluster (hlc1-3) and a copy of a populated database.
3. Routes moved under `/api/v1` with English keys and values; dashboard, CLI, e2e and docs in the same PR set.
4. The whole-cluster update mode of the rolling update, drilled in the lab (success, a node failing, the rollback).
5. `410 Gone` for old routes, CLI version check, OpenAPI contract check in CI.

## Answered (Antho, 2026-10-17)

1. A task that ended well is `succeeded` (`running` / `pending` / `succeeded` / `failed` / `cancelled`).
2. Error `code`s are a fixed list documented in `docs/api.md`; a test fails on a code that is not listed.
3. The migration is also tested on a copy of serveur-antho's database, on the lab only, deleted after the test.
4. This change comes first: HA steps 2-4, the clean package, phase E and leaving `/root` are written after it, once,
   with the new names.
5. The read-only role is `viewer` (today `observateur`; the French dashboard still shows « Observateur »).
6. `snapshots` everywhere (never `instantanes`, never `checkpoints`, which replication already uses).
