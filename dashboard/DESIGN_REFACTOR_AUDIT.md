# HyperLite dashboard — design refactor audit

> Phase 0 and phase 1 of the design refactor. **Documentation only: no application file, test,
> dependency, route, style or backend file was modified.**
> Base: branch `test`, commit `e70bef6` (merge of #173 `feat/frontend-rebuild`).
> Goal of the refactor: apply the validated HyperLite identity (Enclave mark, Porphyre palette,
> "Soft" shapes, IBM Plex type) to every frontend screen, with zero functional regression.

This audit builds on the existing, much larger preservation work in `docs/frontend-rebuild/`
(`00-audit-technique.md`, `01-matrice-preservation-fonctionnelle.md` with 1,115 feature rows,
`08-avancement-phase-2.md`). Figures below were re-extracted from the code on `e70bef6`; where they
restate those documents, they were checked against the source.

---

## 1. Phase 0 — branch and environment

| Check | Result |
|---|---|
| Branch | `test` (tracks `origin/test`), clean working tree |
| `test` vs `master` | `test` = `e70bef6`, `master` = `8d0ad1d`; work stays on `test` |
| Repository rules (`CLAUDE.md`, `CONTRIBUTING.md`) | English only in the repository; French wire identifiers (`nom`, `etat`, ...) never renamed; no push to `master`, no force-push; the project normally expects a feature branch from `test` plus a pull request |
| Frontend commands | `npm ci`, `npm run lint`, `npm test` (Vitest), `npm run build` (legacy UI default) / `VITE_DEFAULT_UI=next npx vite build`, `npx playwright test` (needs a real backend and libvirt, see `e2e/start-backend.sh`) |
| Node | v22 available (>= 20.19 required) |
| **Blocker** | The sandbox used for this audit cannot reach `registry.npmjs.org` or `pypi.org` (egress allow-list). `npm ci` fails with HTTP 403, so **lint, unit tests, build and screenshots cannot be run here yet**. E2E tests also need libvirt, which this sandbox does not have |
| Push | No GitHub credentials in the sandbox: commits stay local until a push method is agreed |

## 2. Stack

| Layer | Details |
|---|---|
| Framework | React 19.3, Vite 8.3, `react-router-dom` 7.18 (BrowserRouter) |
| Styling | Two systems side by side: (1) **`src/next/next.css`** — 593 lines, ~191 `.nx-*` classes, CSS custom properties scoped to `:root[data-ui="next"]`, used by the rebuilt interface; (2) **Tailwind 4 + shadcn/ui** — `src/index.css` (`--background`, `--primary`, ... plus the `anthracite`/`chrome` ramps), used by the legacy interface and by the components the rebuilt interface still reuses |
| UI primitives | shadcn/ui on `radix-ui` (27 files in `components/ui/`), `cmdk`, `sonner` toasts, `lucide-react` icons, `recharts` |
| State | Zustand: `useInfraStore`, `useAuthStore`, `useConfirmStore`, `usePromptStore`, plus `next/tokens/theme.js`, `next/i18n`, `next/explorer/store.js` |
| API | `src/api/client.js` — 153 exported functions over `realFetch` (Bearer token, error normalisation, 401 handler); no `/api` prefix |
| i18n | Rebuilt UI only: `next/i18n/en.js` (959 lines) and `fr.js` (992 lines), parity covered by tests. Legacy UI: inline English |
| Fonts | Rebuilt UI: self-hosted Inter (UI) + IBM Plex Mono; **IBM Plex Sans 400/500/600 woff2 are already in `src/next/fonts/`** (unused). Legacy UI and login: Archivo + Plex Mono from the Google Fonts CDN (`index.html`) |
| Themes | Rebuilt UI: dark (default), light, system (`hyperlite-next-theme`). Legacy UI: light default, `.dark` class (`hyperlite-theme`) |

## 3. Two interfaces coexist — decision needed

`App.jsx` renders the **legacy interface by default**. The rebuilt interface (`src/next/`) is used
when the build sets `VITE_DEFAULT_UI=next`, or per browser with `?ui=next` (stored in
`localStorage.hyperlite-ui`). Step 16 of the previous rebuild (retiring the legacy UI) is still
waiting for the owner's approval (`08-avancement-phase-2.md` §9).

Consequences for this refactor:

- Styling both interfaces doubles the work and the regression surface.
- Styling only the rebuilt interface leaves the default build unchanged unless it is built with
  `VITE_DEFAULT_UI=next`.
- `index.css` shadcn tokens are **shared**: changing them restyles the legacy UI too, unless the new
  values are scoped (for example under `[data-ui="next"]` and a login-specific scope).

**Recommendation:** refactor the rebuilt interface plus every screen it shares (login, console
windows, dialogs, toasts, retained modals); leave the legacy screens untouched until their retirement
is decided. See question Q1.

## 4. Routes and deep links (must all keep working)

| Route | Component | Guard |
|---|---|---|
| `/` | redirect to `/datacenter` | session |
| `/datacenter?tab=<page>` | Workspace (datacenter) | session |
| `/node/:id?tab=<page>` | Workspace (node; `local` = host running HyperLite) | session |
| `/vm/:id?tab=<page>` | Workspace (VM) | session |
| `*` | redirect to `/datacenter` (no 404 page) | session |
| `/console/:name?mode=terminal` | `ConsoleWindow` (noVNC or terminal, separate window) | own session gate |
| `/host-shell` | `HostShellWindow` | session + admin |
| `/container-terminal/:name` | `ContainerTerminalWindow` | session |
| (anonymous) | `LoginScreen` at the requested URL; SSO through `?sso_token=` / `?sso_error=` | — |
| query `?ui=next` / `?ui=legacy` | interface switch (read once, then removed from the URL) | — |

### Page ids (`?tab=`), from `next/legacy/tabs.js`

| Object | Top tabs → pages |
|---|---|
| Datacenter | Summary → `summary` · Monitor → `activity`, `journal` · Configure → `nodes`, `ha`, `compat`, `storage`, `reseau`, `templates`, `backups`, `exports`, `automation`, `notifications` · Permissions → `permissions`, `sso` · Containers → `containers` · VMs → `vms` · Snapshots → `snapshots` (18 pages) |
| Node | Summary → `summary` · Monitor → `system`, `tasks` · Configure → `network`, `disk`, `compat`, `shell` (7 pages) |
| VM | Summary · Console · Configure → `hardware`, `options` · Snapshots · Backup (6 pages) |

Unknown `?tab=` values fall back to the first tab (`locate()`); every historical id stays valid.

## 5. Shell of the rebuilt interface

| Area | Content to preserve |
|---|---|
| Sidebar (`next/layout/Sidebar.jsx`) | Brand block; resizable width (`separator`, keyboard); collapse (Ctrl+B, stored in `hyperlite-next-sidebar`); drawer under 1024 px (Escape, scrim); 5 groups — Infrastructure (Overview, Nodes + count, High availability, Compatibility), Management (VMs + count, Containers + count, Storage, Network, Backups), Observability (Monitoring, Alerts + count/tone → opens the dock, System logs), Administration — admin only (Users & roles, Settings = notifications), More (ISO & templates, Snapshots, Exports, Automation); Inventory Explorer with a horizontal splitter; user menu (role, language, theme, updates — admin, account security, classic interface link when not forced, sign out) |
| Inventory Explorer (`next/explorer/`) | Server / Pool modes, search "Find a node or VM", highlighted hits, keyboard tree (roving tabindex), context menu, favourites, recents, 4 views, windowing (23 e2e tests) |
| Top bar | Sidebar toggle, breadcrumb, Find button (Ctrl+K and `/`), tasks button with running count, create menu (VM wizard, container dialog), notifications/alerts |
| Palette (Ctrl+K) | Navigation and commands, opens wizards |
| Dock | Tasks, alerts, logs panels; collapsed by default with running count |
| Global | Skip link, `Toaster` (bottom right), `ConfirmHost` (promise-based confirm dialog), polling every 6 s (inventory) and 15 s (extras) |

## 6. Screen inventory

Counts extracted from the source (API = distinct `api/client.js` functions called; controls = buttons,
inputs; confirm = confirmation calls). The exhaustive list of actions per screen is matrix `01`.

| Screen / file | Lines | API | Buttons | Inputs | Confirms |
|---|---:|---:|---:|---:|---:|
| Overview (`pages/Overview.jsx`) | 147 | 2 | 16 | 0 | 0 |
| VM list (`VmList.jsx` + `components/VmCollection.jsx`) | 147 | 0 | 16 | 1 | 0 |
| Snapshots (global) | 51 | 1 | 4 | 0 | 0 |
| Activity (also node Tasks) | 118 | 2 | 6 | 5 | 0 |
| Journal | 83 | 2 | 4 | 6 | 0 |
| Storage | 168 | 5 | 15 | 6 | 3 |
| Network | 151 | 6 | 11 | 2 | 2 |
| ISO & templates | 86 | 3 | 6 | 0 | 2 |
| Backups | 71 | 2 | 6 | 0 | 2 |
| Exports | 64 | 3 | 5 | 0 | 2 |
| Security (users, groups, pools, custom roles, assignments) | 276 | 23 | 32 | 14 | 9 |
| SSO | 109 | 2 | 5 | 3 | 2 |
| Notifications | 166 | 6 | 17 | 10 | 2 |
| Automation | 203 | 7 | 24 | 8 | 3 |
| Containers | 220 | 9 | 28 | 7 | 4 |
| Nodes | 127 | 5 | 13 | 4 | 2 |
| High availability | 97 | 3 | 7 | 1 | 3 |
| Compatibility | 72 | 1 | 0 | 1 | 0 |
| Node summary | 105 | 3 | 11 | 0 | 0 |
| Node system / network / disk / compat / shell (`NodePages.jsx`) | 254 | 4 | 5 | 1 | 0 |
| VM summary | 114 | 4 | 0 | 0 | 0 |
| VM console launcher | 46 | 0 | 5 | 0 | 0 |
| VM hardware + options (`VmConfigure.jsx`) | 292 | 13 | 18 | 8 | 3 |
| VM snapshots + backup | 253 | 12 | 23 | 3 | 6 |
| VM creation wizard (8 steps) | 345 | 4 | 18 | 13 | 2 |
| Container creation dialog | 117 | 2 | 7 | 7 | 2 |
| **Subtotal rebuilt pages** | | | **302** | **100** | **49** |

### Screens and components shared with the legacy styling (Tailwind/shadcn)

| Component | Where it appears in the rebuilt UI | Notes |
|---|---|---|
| `auth/LoginScreen.jsx` | Before any interface | Password, TOTP second step (6 digits), SSO button when enabled, `?sso_error`, errors in `role="alert"`, deep link kept after sign-in; uses `HyperliteLogo` |
| `console/ConsoleWindow.jsx`, `HostShellWindow.jsx`, `ContainerTerminalWindow.jsx` (+ `ConsolePanel`, `HostShellPanel`, `ContainerShellPanel`) | Separate windows | noVNC and xterm assets in `public/`; VM state probe every 5 s |
| `AccountSecurityModal.jsx` | User menu | 2FA enrolment, API tokens (6 API calls) |
| `UpdateModal.jsx` | User menu (admin) | Update task polling, `/health` |
| `FirewallRulesEditor.jsx`, `IsoUploadDropzone.jsx`, `VmDiskUploadDropzone.jsx`, `DriversIsoControl.jsx`, `DeploymentProfileCard.jsx`, `MetricsHistoryCard.jsx`, `ProvisioningBar.jsx`, `CompatChecks.jsx` | Inside rebuilt pages | XHR uploads with progress |
| `ConfirmHost` / `ConfirmDialog` (shadcn AlertDialog), `PromptDialog`, `sonner` toasts | Everywhere | Wording and focus handling must be kept |

### Transversal states already implemented

`EmptyState`, `ErrorState` (retry + copy message), `Skeleton`, `PermissionNotice`,
`StatusIndicator`, "Not provided by the API" values, bounded task follow-up, disabled actions with a
reason (`MenuItem reason`). Missing: dedicated 404 page (unknown routes redirect), global error
boundary screen, offline/maintenance screen, session-expiry warning (JWT 4 h, see `00` §B9).

## 7. Data flows that must not change

- 151 of the 153 client functions are used by the frontend (147 called directly, `startVM`,
  `stopVM`, `restartVM`, `deleteVM` through the action map of `useInfraStore`); `deleteVmDisk` and
  `fetchContainer` have no caller, as already noted in `00`. The rebuilt interface calls 108 of them
  directly. Other calls: `useAuthStore`, `/health`, two XHR uploads, the SSO link and
  `window.open` for export downloads.
- Polling cadences: inventory 6 s, extras 15 s, VM live metrics 4 s, provisioning 6 s, activity 8 s,
  console probe 5 s, update task 1–2 s.
- Permission model on the client: `lib/capabilities.js` (`capabilities(role)`,
  `vmActionState(action, vm, caps)`); admin-only groups and actions are hidden or disabled with a
  reason. Backend stays the source of truth.
- URL sync: `lib/urls.js`, `useUrlSync`, `?tab=` ids above.

**The refactor will not touch** `api/client.js`, the stores, `lib/*.js` logic, polling, capabilities,
URL helpers, wizard submit logic, upload logic, or any request, payload, header or cache.

## 8. Tests and selectors to protect

| Suite | Content | Sensitivity to a redesign |
|---|---|---|
| Vitest (7 files) | `capabilities`, `format`, `labels`, `next-inventory`, `next-lib`, **`next-tokens`** (WCAG contrast of every `next.css` token pair), `windowsProfile` | `next-tokens.test.js` parses the two token blocks of `next.css`: new values must pass it (primary text ≥ 7:1, secondary/muted/functional colours ≥ 4.5:1 on every surface, focus/strong border ≥ 3:1, text on accent ≥ 4.5:1) |
| Playwright (24 specs, 11 `next-*`) | Real backend + libvirt; audit of every page in 2 themes × 2 languages, 5 widths, keyboard, axe | 878 `getByRole` calls → **accessible roles and names (i18n strings) are the contract**; 43 `locator()` calls on `.nx-root`, `.nx-sidebar-user`, `.nx-root[data-sidebar="open"]`, `.nx-results`, `.nx-nav-scroll`, `.nx-chev`, `mark.nx-hit`, `.nx-sidebar`, `.nx-top`, `.nx-main h1`, `[role="treeitem"][tabindex="0"]`, `#sec-pools`, `#sec-groups`, `input[type=password]` |
| `data-testid` | none in the code base | — |

Rule for the refactor: keep every role, accessible name, i18n string used as a name, and every class
or id listed above (restyle them, do not rename them).

## 9. Gap analysis against the validated HyperLite direction

| Topic | Current rebuilt UI | Validated direction | Change |
|---|---|---|---|
| Brand mark | Sidebar: an "H" in a rounded square (forbidden cliché); login and legacy: 5 slanted bars on navy (`HyperliteLogo.jsx`, `public/favicon.svg`) | Enclave mark (two side rails, modular core) on a 32-unit grid | New `EnclaveMark` component, favicon (light/dark aware), app icon; `HyperliteLogo` becomes Enclave for the screens that use it |
| Palette | Navy `#0B1220` + cyan `#38BDF8` accent + indigo `#7A7DF3` secondary (the "dark + cyan" look to avoid) | Porphyre: plum `#6E3550` (light) / `#CE9DB2` (dark), graphite-plum neutrals, steel-teal telemetry, desaturated status colours | Replace both token blocks in `next.css`; keep token names |
| Brand share | Cyan used for links, focus, selection, charts | Brand ≤ 8 % of dense screens; charts use the telemetry hue; borders neutral | Separate `--color-accent` (brand) from chart and info colours |
| Type | Inter UI + Plex Mono | IBM Plex Sans + Plex Mono | Switch `--font-ui`; the woff2 files are already bundled |
| Shapes | radius 2/4/6/10, card shadow | "Soft": 4 / 6 / 8 / 12 / 16 px, no card shadow, flat surfaces, thin borders, progress bars with round ends | Update radius tokens, drop `--shadow-card`, keep a light `--shadow-float` for menus only |
| Login | shadcn card, Archivo, CDN fonts, 5-bar logo | Enclave, Plex Sans, discreet rail motif, light and dark | Restyle within the existing component; no logic change |
| Console windows | Legacy styling | Neutral near-black console, compact toolbar, explicit connection state, very discreet brand | Restyle the window chrome only |
| Charts | Recharts / custom sparklines with the accent colour | Telemetry hue, second series in a lighter tint, thresholds in warning colour | Colour props only |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Shared shadcn tokens restyle the legacy UI | Scope the new values; verify the legacy UI builds and looks unchanged |
| Contrast test fails on new values | Values pre-computed; the dark theme needs a brand tone light enough to be text (`#CE9DB2`) while the text on accent is dark — may need a separate button-fill token, added to the test rather than weakening it |
| e2e selectors / names | No rename of roles, names, i18n strings used as names, listed classes and ids |
| Hidden behaviour in markup (event handlers, `aria-*`, keyboard handlers) | Restyle through CSS first; JSX changes limited to presentation wrappers, reviewed diff by diff |
| No build or test in this sandbox yet | Resolve Q3 before any code commit; otherwise every commit is checked by the owner's CI / dev instance |
| Several contributors on `test` | Small commits, rebase before each push, no force-push |

## 11. Proposed plan (after validation)

1. **Tokens** — `next.css` light and dark blocks to Porphyre + Soft; Plex Sans; contrast test green.
2. **Brand** — `EnclaveMark`, favicon, app icon, sidebar brand, `HyperliteLogo` → Enclave.
3. **Shell** — sidebar, top bar, dock, palette, explorer states (active rail, focus, counts).
4. **Components** — buttons, inputs, badges, tables, menus, dialogs, toasts, empty/error/skeleton.
5. **Screens** — overview, VM list and detail tabs, node pages, storage, network, protection,
   observability, administration, wizards — one commit per domain.
6. **Shared screens** — login, console windows, retained modals.
7. **Missing states** (only with approval) — 404 page, global error screen, session-expiry notice.
8. **Verification** — lint, Vitest, build (both UIs), Playwright suites, screenshots in both themes.

Each step is a separate commit on `test`, frontend only.

## 12. Open questions for the owner

- **Q1** — Which interface do we refactor: the rebuilt interface only (recommended), both, or should
  the rebuilt one become the default (`VITE_DEFAULT_UI=next`) as part of this work?
- **Q2** — Default theme: keep dark as the default of the rebuilt UI, or switch to light / system?
- **Q3** — Verification environment: allow `registry.npmjs.org` (and `pypi.org`,
  `files.pythonhosted.org` for the backend) in the session's network settings, or run
  `npm run lint && npm test && npm run build` and the Playwright suites on your side after each step?
- **Q4** — Push: provide a fine-grained token (this repository only, Contents read/write), or receive
  the commits as a patch / bundle and push them yourself?
- **Q5** — The project rules ask for a feature branch from `test` and a pull request. Confirm that
  commits go directly on `test`, or use `design/hyperlite-identity` → PR into `test`.
- **Q6** — Missing states (404, global error, session expiry): add them in this refactor or later?
