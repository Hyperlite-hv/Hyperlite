# Web UI test matrix

End-to-end validation of the Hyperlite web UI, driven like a real user with Playwright
(Chromium) against a **real backend**: a throwaway Hyperlite instance on loopback with its own
SQLite database, talking to a real libvirt/QEMU host (nested virtualization). Every resource the
tests create is prefixed `e2e-` and removed afterwards; the suite never touches an existing VM,
pool or network.

## How to run

```bash
cd dashboard
npm ci && npx playwright install chromium
npm run build          # the backend serves dashboard/dist
npm run test:e2e       # starts its own backend via e2e/start-backend.sh
```

Requirements: a Linux host with libvirt/QEMU (`/dev/kvm` recommended), the backend virtualenv at
`../venv`, and the `hyperlite-isolated` libvirt network. Set `E2E_BASE_URL` to test an already
running instance instead. `E2E_ALL_BROWSERS=1` adds the Firefox and WebKit projects (install them with `npx playwright install --with-deps firefox webkit`; use `npm run test:e2e:all-browsers`). Reports, traces and screenshots go to `e2e-report/` and `e2e-results/`
(git-ignored). CI runs the unit and API suites as required checks and the E2E suite as an advisory `e2e` job
(libvirt is installed on the hosted runner; it becomes required once it has proven stable).

## Status legend

PASS · FAIL · BLOCKED (cannot be tested in this environment, reason given) ·
NOT IMPLEMENTED (the product has no such feature) · NOT TESTED · PARTIALLY TESTED
(never PASS while an essential part is unverified).

## Matrix

| Feature | User journey | Real backend | Automated test | Result | Remaining issue |
|---|---|---|---|---|---|
| Sign in / sign out | Wrong password, unknown user, empty form, sign in, reload, sign out | Yes | `auth.spec.ts` | PASS | - |
| Session handling | Invalid or expired token returns to sign-in with an explanation; deep link lands on the requested page after sign-in | Yes (token rejection also simulated by route mocking) | `auth.spec.ts`, `degraded.spec.ts` | PASS | - |
| Brute-force protection | Repeated failures lock the account; UI reports rate limit (429) | Yes | `auth.spec.ts`, `degraded.spec.ts` | PASS | Per-IP limit not exercised in the UI |
| Two-factor authentication | Enable with a computed TOTP code, sign in with a code, reject a wrong code, disable | Yes | `settings.spec.ts` | PASS | - |
| API tokens | Created, shown once, usable without a session, refused after revocation | Yes | `settings.spec.ts` | PASS | - |
| SSO (OIDC) settings | Values persist; client secret is never returned by the API | Yes | `settings.spec.ts` | PARTIALLY TESTED | The redirect/callback flow needs an identity provider and is not exercised here |
| Users and roles | Create a read-only user, duplicate name refused, delete after confirmation | Yes | `users.spec.ts` | PASS | - |
| Authorization | Read-only user does not see admin actions and the backend refuses the calls (403); cannot read channel secrets | Yes | `users.spec.ts` | PASS | Per-VM ACL matrix not covered |
| Navigation | Tab kept in URL and after reload, unknown tab/route fall back, selection resets tab | Yes | `nav.spec.ts` | PASS | - |
| Page rendering | Every Datacenter tab and node tab renders without console errors, failed requests or French text | Yes | `pages.spec.ts` | PASS | Data correctness of each dashboard tile vs API not asserted |
| Dashboard summary | Tiles, charts, tables render | Yes | `pages.spec.ts` | PARTIALLY TESTED | Rendering only; values not compared with the API |
| VM creation wizard | Field validation, creation with task follow-up, duplicate name (409), out-of-range resources (422) | Yes | `vm.spec.ts` | PASS | - |
| VM lifecycle | Start, no second start offered, force stop after confirmation, edit memory when stopped, memory refused while running | Yes | `vm.spec.ts` | PASS | Graceful shutdown depends on guest ACPI; not asserted |
| VM console | Graphical console window opens for a running VM | Yes | `vm.spec.ts` | PARTIALLY TESTED | Window opening only; no pixels/keystrokes verified |
| VM deletion | Confirmation naming the VM, disk removed | Yes | `vm.spec.ts` | PASS | - |
| Snapshots | One snapshot on double-click then deleted; restore after a confirmation naming the snapshot (cancel changes nothing, confirm brings the VM configuration back) | Yes | `vm.spec.ts`, `coverage.spec.ts` | PASS | Restore of a running VM with memory not covered |
| Clone | Clone after a name prompt; cancel creates nothing | Yes | `vm-advanced.spec.ts` | PASS | - |
| Templates | Convert to template, deploy a new VM | Yes | `vm-advanced.spec.ts` | PASS | - |
| Backups | Cold backup created, restored to a new VM, restored VM starts | Yes | `vm-advanced.spec.ts` | PASS | Hot backup and scheduled jobs not covered |
| Disk export | Export listed, one-time download ticket works once then is refused | Yes | `vm-advanced.spec.ts` | PASS | Import of an exported disk not covered |
| Recent activity | Real operations listed with status, status filter | Yes | `vm-advanced.spec.ts` | PASS | - |
| Journal | Actions recorded, result filter works | Yes | `settings.spec.ts` | PASS | - |
| Storage pools | Create a directory pool, persists, remove; default pool and duplicate refused | Yes | `settings.spec.ts` | PASS | NFS and ZFS pools not covered (need a server / ZFS module) |
| Virtual networks | Create an isolated network, duplicate and invalid bridge refused, delete after confirmation | Yes | `network.spec.ts` | PASS | Network firewall rules not covered |
| ISO images | Upload, list, cancel and confirm deletion | Yes | `destructive.spec.ts` | PASS | - |
| Notifications | Test button delivers a real request to a local webhook receiver and a real SMTP message to a local SMTP receiver; non-http(s) URL refused; SMTP password never returned; unreachable SMTP server gives a readable error | Yes | `settings.spec.ts`, `coverage.spec.ts` | PARTIALLY TESTED | Email channel is created through the API in the test: the UI form always enables STARTTLS, and the local test receiver has none |
| Deployment profile | Choice persists after reload | Yes | `settings.spec.ts` | PASS | - |
| Destructive actions | Confirmation dialog names the resource; cancel keeps it | Yes | `destructive.spec.ts`, `vm.spec.ts`, `network.spec.ts`, `users.spec.ts` | PASS | Automation jobs, HA, containers, nodes share the same dialog but are not each exercised |
| Degraded modes | 401, 403, 409, 422, 429, 500, malformed JSON, offline and recovery, slow and never-ending loads, double-click | Mixed: statuses forced by route mocking, the UI behavior is real | `degraded.spec.ts` | PASS | 404 handled by the generic error path; not asserted separately |
| Keyboard use | Tab order, visible focus, dialogs trap focus and close on Escape, tree and tabs operable | Yes | `keyboard-responsive.spec.ts` | PASS | - |
| Accessibility scan | No serious or critical axe violations on the audited pages | Yes | `pages.spec.ts` | PARTIALLY TESTED | Automated scan does not replace a screen-reader review |
| Responsive layout | 1920, 1366, 820, 390 and 640 px: no horizontal scroll, actions reachable, phone menu | Yes | `keyboard-responsive.spec.ts` | PASS | Chromium only |
| Automation (jobs) | Create a host job, dry run does not execute, real run executes, failing command recorded as failed, duplicate name refused, delete after a confirmation naming the job, read-only user refused by the backend | Yes | `automation.spec.ts` | PASS | Jobs targeting VMs (SSH into a guest) not covered |
| Containers (LXC) | Tab renders | Yes | `pages.spec.ts` | PARTIALLY TESTED | Creation needs image download and LXC driver; not covered |
| Nodes, migration | Tab renders; single node | Loopback only | `pages.spec.ts` | BLOCKED | Adding a second node and live migration need a second hypervisor host |
| High availability | Tab renders | Yes | `pages.spec.ts` | BLOCKED | Failure detection and recovery need at least two nodes and shared storage |
| Updates | Update dialog opens | Yes | `keyboard-responsive.spec.ts` | PARTIALLY TESTED | Applying an update needs the APT repository and is destructive |
| Multi-user concurrency | A user deleted by an administrator is signed out on reload while the admin session keeps working; a change made in one admin session is visible in another after reload | Yes | `coverage.spec.ts` | PARTIALLY TESTED | No live push between sessions (the UI refreshes on reload or polling); simultaneous edits of the same resource not covered |
| Browsers | Full suite on Chromium, Firefox and WebKit (one fresh backend per browser, `npm run test:e2e:all-browsers`) | Yes | all | PASS | Mobile browsers and real Safari not run |

## Defects found and fixed while writing the suite

- Selected tab was lost on reload: now stored in the URL.
- Several destructive actions used the native `window.confirm`: replaced by an accessible
  confirmation dialog that names the resource.
- A rejected session left the UI in a broken state: the global 401 handler now returns to sign-in
  with an explanation.
- Wrong 2FA code or wrong password inside a session returned 401 (which logs the user out):
  now 400.
- Notification webhook URLs were not validated at creation: now rejected with 422.
- Pages could show an endless spinner or a raw JavaScript error: shared loading and error states.
- Missing accessible names, roles, focus handling and low-contrast colors on many controls.
- French wording left in permission role labels and event names.
- Job buttons of the Automation tab and the snapshot Restore button had no per-item accessible name.
- A French error message remained in the email notification sender.
- Deploy dialog of the Templates tab was not an accessible dialog.
- Converting a VM to a template left its cloud-init ISO behind: now removed.
- Restoring a backup to a new VM ignored the original vCPU, memory and network (1 vCPU, 1 GiB,
  `default`): the settings are now recorded with the backup and reused; older backups keep the
  historical defaults.
