# Design refactor report: the "next" dashboard

Branch `design/refonte`, one pull request into `test`. Brief: `REFONTE-COMPLETE.md` (rules R1 to R13, the order of work in section 6) and the `app.html` mockup.

## Deviations from the brief, agreed with the owner

- **Backend changes.** The brief says "frontend only". Several screens of the mockup show data the API did not return (live node load, hardware inventory, storage history, disk sizes, interface models, last sign-in, audit IP…). The owner asked to add what is missing to the API rather than drop it, so the first commit (`feat(api): data the redesigned dashboard needs`) extends the backend, with tests. No existing field was renamed or removed; every change is additive.
- **Undoing earlier work.** The owner asked to apply the redesign even where it undoes earlier choices (for example "Snapshot" and "Migrer" leaving the VM header).
- **One commit per item.** The 17 items map to 15 commits: items 11 to 14 (VM header, summary, hardware, snapshots and backups) touch the same files and were committed together as `feat(dashboard): VM pages without repeated operations`; items 15 and 16 (wizards, login) share one commit. `style: sort imports in the SSO router` is a lint fix-up.

| Item | Commit |
|---|---|
| API additions | `feat(api): data the redesigned dashboard needs` |
| 1 | `feat(dashboard): shared redesign components and tokens` |
| 2 | `feat(dashboard): regroup the sidebar by task and simplify the top bar` |
| 3 | `feat(dashboard): datacenter list pages without repeated titles` |
| 4 | `feat(dashboard): library page for ISO images and templates` |
| 5 | `feat(dashboard): tasks and audit log filter bars and tables` |
| 6 | `feat(dashboard): users and roles in sub-tabs, SSO and notifications layouts` |
| 7 | `feat(dashboard): automation tasks as cards with their history beside` |
| 8 | `feat(dashboard): home page with the attention banner, KPI strip and watch list` |
| 9 | `feat(dashboard): virtual machines list with a detail panel` |
| 10 | `feat(dashboard): node pages with flat tabs, a KPI strip and real hardware data` |
| 11–14 | `feat(dashboard): VM pages without repeated operations` |
| 15–16 | `feat(dashboard): three-column VM wizard, container dialog and sign-in screen` |
| 17 | `feat(dashboard): translate the remaining English labels and screen-reader names` |

## API additions

All additive, covered by `tests/test_live_inventory_api.py` (and the SSO test).

| Endpoint / field | What it gives |
|---|---|
| `GET /nodes` (`live`), `GET /nodes/{name}/summary`, `GET /dashboard` | Live CPU, memory, uptime, cores, CPU model, kernel, OS and versions of every node, recorded by the metrics collector in the new `node_live` table (remote nodes are probed over the existing SSH link). |
| `GET /nodes/{name}/metrics/history` | CPU / memory / disk / network history of a node. |
| `GET /vms/{name}/metrics/history?node=` | History of a VM on a remote node (the collector now samples remote VMs). |
| `GET /storage/history?range&node` | Pool usage history (`storage_samples`), for the home page sparklines. |
| `GET /nodes/{name}/hardware` | Network interfaces (speed, MAC, addresses), physical disks (`lsblk`, SMART health when `smartctl` is present), CPU (`lscpu`). |
| `POST /nodes/test` | Tests the SSH connection before adding a node. |
| `GET /vms/{name}/disks`, `/interfaces` | Disk size, allocation and pool; interface model, VLAN and firewall state; `?node=`. |
| `GET /networks` | DHCP range and attached VM count. |
| `GET /storage/pools` | Path and node. `GET /isos`: added date and location. |
| `POST /auth/login` (`remember`) | "Stay signed in": a 7-day session. `users` gain `last_login_at` and `totp_enabled` in the listing. |
| Audit log | The client IP is recorded (`audit_log.ip`) and `GET /audit/count` returns the total for the footer. |
| `GET /backup-schedules` | Every schedule at once (the VM list and home page no longer loop per VM). |
| `PUT /vms/{name}/settings` (`os_label`) | The OS type is editable with vCPU and memory. |
| `POST /auth/sso/test` | Tests the OIDC discovery document from the SSO page. |

## Page by page

### Shell (items 1, 2)

- New shared components in `components/ui.jsx` (`PageHeader`, `Freshness`, `KpiStrip`, `Meter`, `Spark`, `Pill`, `StatePill`, `Chip`, `Card`, `Empty`, `SideDrawer`, `Field`, `HelpTip`) and the redesign styles in `refonte.css`, built only on the Porphyre tokens of `next.css`.
- **Sidebar** regrouped by task: Accueil · Infrastructure (Nœuds, Machines virtuelles, Conteneurs, Stockage, Réseau) · Cluster (Haute disponibilité, Compatibilité) · Protection (Sauvegardes, Snapshots, Exports) · Bibliothèque · Opérations (Tâches, Journal d'audit, Automatisation) · Administration (Utilisateurs et rôles, SSO, Notifications). `aria-current` on the active entry.
- **Top bar:** clickable breadcrumbs, search, **one "Activité" button with a badge** (alerts, or running tasks) that opens the drawer, and **"Créer ▾" as a default button** listing VM, Conteneur, Pool, Réseau, Utilisateur (the last three open the create drawer of their page through `lib/intents.js`).
- **Activity drawer:** tabs Alertes / Tâches / Journal, with a footer linking to the Tâches and Journal d'audit pages.

### Datacenter pages (items 3 to 7)

- Every list page has **one** `h1` with its count, a one-line description and one primary; the repeated card titles are gone (R1). Pages that render their own header set `Page.ownHeader`.
- **Nœuds:** table with state, connection, CPU / memory / storage meters from the live data, VM count, libvirt version, uptime. "Ajouter un nœud" opens a side drawer (key to authorize, form, **Tester** then **Tester et ajouter**).
- **Stockage:** pools only (the ISO images moved to Bibliothèque), create drawer (directory / NFS / ZFS, node), volumes expand per row.
- **Réseau:** networks table with autostart, mode, bridge, subnet, DHCP range, VM count; create drawer; firewall per network.
- **Conteneurs:** created only from the wizard (the page primary and "Créer ▾"); backups card.
- **Haute disponibilité:** freshness, prerequisites banner. **Compatibilité:** yes/no matrix and allocation card, fully translated.
- **Sauvegardes, Snapshots, Exports:** tables without repeated titles.
- **Bibliothèque** (new, item 4): tabs Images ISO (upload drop zone, list with date and location) / Modèles.
- **Tâches and Journal d'audit** (item 5): filter bars (search, state/type, period), dense tables, the audit IP column and a footer count.
- **Utilisateurs et rôles** (item 6): sub-tabs Utilisateurs / Groupes / Rôles / Pools de VM / Attributions, each with one explicit primary and its form in a side drawer; global roles read-only in Rôles. **SSO:** enable switch and "Tester la connexion". **Notifications:** channel list and a side drawer.
- **Automatisation** (item 7): tasks as cards, their run history beside.

### Accueil (item 8, R13)

Attention banner, KPI strip, Nœuds table, "À surveiller" list, pools with usage sparklines, recent activity. The full charts and the range selector live only in the Performances tab (with a node selector); Événements is the full feed.

### Machines virtuelles (item 9)

Variant B: a filterable table and a detail panel for the selected VM.

### Node (item 10, R4, R5)

Flat tabs Résumé · Performances · Système · Réseau · Stockage · Tâches · Compatibilité · Shell. Résumé: KPI strip with sparklines, "Machines virtuelles sur ce nœud" table plus "Toutes les VM", Configuration (including the alert state), recent activity. Système, Réseau (interfaces) and Stockage (capacity plus physical disks) read `GET /nodes/{name}/hardware`. The Shell tab embeds the terminal.

### VM (items 11 to 14, R6 to R10)

- **Header:** contextual primary (Console when running, Démarrer when stopped), Arrêter when running, and **one Actions ▾ menu** grouped Alimentation / Protection / Cycle de vie, then Copier le lien / Copier l'IP, then Supprimer la VM (danger).
- **Résumé:** 4 KPI tiles with sparklines linking to Performances, Configuration, Protection card ("Planifier", "Voir", HA state), recent activity. "Toutes les opérations" is deleted (R7).
- **Matériel › Processeur et mémoire** is the editable form (vCPU, mémoire, type d'OS, "Pris en compte au prochain démarrage"); disks table with an add drawer; drivers card. **Options et limites** keeps only the live cgroups limits with one "Appliquer à chaud" button (R9).
- **Réseau:** interfaces card (network, model chip, MAC, VLAN, firewall state, add drawer) and the firewall card (default policy in the card head, rules, "Ajouter une règle" / "Appliquer les règles" in the footer).
- **Console:** embedded VNC / SSH.
- **Snapshots:** timeline, "Créer un snapshot" is the tab primary. **Sauvegardes:** "Sauvegarder maintenant" is the tab primary, the schedule card has a switch and a default "Enregistrer" (R10).

### Wizards and login (items 15, 16)

- **Create VM:** three columns (steps, form with tiles, "Votre VM" recap), footer with the step count, Annuler, Suivant.
- **Create container:** tiles and a first-build banner.
- **Sign-in:** a brand pane and the form card, show/hide password, "Rester connecté" (7-day session), the TOTP step and SSO unchanged.

### i18n (item 17)

Every `aria-label` of the rebuilt interface goes through `t("a11y.*")`; the Compatibility, Automation, drivers, firewall and backup-frequency strings listed in rule 6 are translated; the shared confirm / prompt dialogs and the compatibility checks now take translated labels from the rebuilt shell (English stays the default for the historical UI). Key parity between `en.js` and `fr.js` is enforced by the existing test.

## Controls removed or moved

| Before | Now |
|---|---|
| Sidebar "Alertes" (opened the drawer) | Top bar "Activité" button, drawer Alertes tab |
| Top bar "Tâches" button | Top bar "Activité" button, drawer Tâches tab |
| Sidebar "Journaux système" | Drawer Journal tab; the audit trail is Opérations › Journal d'audit |
| Sidebar "Paramètres" (went to Notifications) | Administration › Notifications; SSO now in the sidebar |
| Compatibilité under Protection | Cluster › Compatibilité |
| ISO images in Stockage and in "Images ISO & Templates" | Bibliothèque › Images ISO / Modèles |
| Node header "Nouvelle VM" | Node Actions ▾ › Créer une VM sur ce nœud (local node) |
| Node header "Shell" button | Shell tab; Actions ▾ › Ouvrir le shell |
| Node "Voir les alertes" link | Alert state in Résumé › Configuration |
| Node Compatibility "Actualiser" button | Node Actions ▾ › Actualiser les capacités |
| Node Résumé "→" links, full charts, full VM collection | Tabs; Performances tab; compact VM table plus "Toutes les VM" |
| Tab "Résumé système" | "Système" |
| VM header "Snapshot" | Actions ▾ › Protection › Créer un snapshot (opens the Snapshots tab form) |
| VM header "Migrer…" | Actions ▾ › Cycle de vie › Migrer… (disabled with its reason) |
| VM "Toutes les opérations" block (Start, Stop, Force stop, Restart, Migrate, Clone, To template, Protect HA, Auto cleanup, Delete) | Header primary / Arrêter and Actions ▾ (every action kept, same confirmations) |
| VM Résumé charts and "→" links | KPI tiles linking to Performances; Protection card buttons |
| VM read-only "Processeur et mémoire" card + vCPU/memory in Options | Editable in Matériel; Options et limites keeps the cgroups limits |
| VM Options "Enregistrer" + "Appliquer" | One "Appliquer à chaud" |
| VM Sauvegardes two plum buttons | Tab primary "Sauvegarder maintenant"; default "Enregistrer" in the schedule card |
| Utilisateurs et rôles: 7 stacked sections, five "Créer" | Sub-tabs, one explicit primary each, side drawers |
| Accueil Résumé charts | Accueil › Performances only |

## End-to-end tests updated

The historical-interface specs (`auth`, `pages`, `nav`, `vm`, `vm-advanced`, `network`, `settings`, `users`, `automation`, `coverage`, `degraded`, `destructive`, `keyboard-responsive`) are unchanged and pass. Every `next-*` spec that targeted a moved control was updated; each assertion still checks the same capability, now where it lives. Assertions were added for the redesign rules themselves (a removed duplicate must stay removed).

| Spec | Why it changed |
|---|---|
| all `next-*` | The sign-in screen has a "Show the password" button, so the password field is matched exactly. |
| `next-explorer` | New sidebar groups (always open) and page titles; node tabs renamed ("System", "Storage"); node summary without charts, VM collection or header "New VM" / "Shell" (now in Actions ▾); VM summary without "All operations" or charts, every operation checked in the grouped Actions menu; one "Activity" button in the top bar; Overview without charts on Summary; Tasks export renamed "Export as CSV"; Exports in the Protection group. |
| `next-cluster` | Nodes: the add form is a side drawer ("Add the node"); the local row is recognized by its connection. Compatibility: the "Differences only" filter only appears with two nodes or more (the single-node test host now checks the "Only one node" note instead). |
| `next-pages` | Storage: one `h1`, create drawer, "Volumes of …" button; the ISO part runs in Library › ISO images and checks the upload is gone from Storage; Network: create drawer and "Details of …"; Journal / Backups / Exports titles are the page `h1`. |
| `next-security` | Users and roles: sub-tabs, one explicit primary each and side drawers. |
| `next-containers` | Creation goes through the container dialog (no inline form); page title is the `h1`. |
| `next-notifications` | Channel form in a side drawer ("Add the channel"). |
| `next-automation` | Job form in a side drawer ("Create a task" / "Create the task"); the run history sits beside the job cards. |
| `next-sso` | Page `h1` "Authentication (SSO)", enable switch, admin-groups label translated like the visible one. |
| `next-node` | System shows the hardware / software cards; Network lists the host interfaces; Storage shows the physical disks. |
| `next-vm-tabs` | vCPU / memory edited in Hardware; Options and limits keeps only "Apply live"; disk and interface forms are drawers; snapshots are a timeline; Snapshot and Migrate are in the Actions menu; the backup schedule has a switch. |
| `next-audit` | VM lifecycle: disks added through the drawer, deletion from Actions ▾ › Delete the VM. The object header actions container is `.nx-oh-acts` (was `.nx-headactions`). |

Fixes found by the run: the node shell hint contrast (axe), the VM wizard step rail now fits a phone (numbers only, labels kept for screen readers), the "Create" button kept an accessible name at phone width, the HA page got its "Refresh" button back, several `aria-label`s built from English literals (network, notifications, SSO, users and roles, ISO file input) are translated, form errors are announced (`role="alert"`), and two text buttons whose `aria-label` differed from their visible text lost the redundant label (WCAG 2.5.3).

## Skipped or limited

- **Create a VM on a remote node:** the VM builder only runs on the local host, so "Créer une VM sur ce nœud" is disabled on remote nodes, with the reason.
- **Shell of a remote node:** the terminal is a local pty; the Shell tab of a remote node explains it instead of opening a terminal.
- **Firewall "Source" column** of the mockup: the rule model has no source address, so the column is not shown.
- **Backend messages** (preflight and compatibility check texts, error details) are generated by the API in English and are shown as is.

## Checks

Run on the development host (hl-devhub, real libvirt) at the head of this branch:

| Check | Result |
|---|---|
| `npm run lint` | clean |
| `npm test` (Vitest, including i18n key parity and the token test in both themes) | 40 / 40 passed |
| `npm run build` | OK |
| `npx playwright test` (Chromium, 1 worker, like CI) | historical-interface specs: all passed on the full run; `next-*` specs: all 56 passed after the updates (last reruns: 47 + 38 covering every file) |
| Backend `ruff check` / `ruff format --check` | clean |
| Backend `pytest` | 273 passed |

The full suite also runs in CI on the pull request.
