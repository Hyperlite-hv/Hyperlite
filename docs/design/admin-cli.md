# Design: `hyperlite-admin`, the administration CLI on a node (#516)

Status: **proposed; the maintainer left section 8 to the best of vSphere and Proxmox VE (2026-10-23), the choices are
recorded there.** Steps 1 to 4 follow, one pull request each.

## 1. Goal

Drive a node, and through it the cluster, from its shell as `qm`, `pct` and `pvesh` do on Proxmox VE: VMs,
containers, storage, networks, backups, tasks and the cluster, **through API v1** (the same checks, the same tasks, the
same audit as the dashboard), with **no token to type as root**, and a JSON output for scripts.

Out of scope: a second implementation of anything. The CLI never calls libvirt, `zfs` or the database itself; a thing
the API cannot do, the CLI cannot do either. The workstation client (`hyperlite`, `cli/`) stays what it is.

## 2. What Proxmox VE does

| | Proxmox VE | What we take |
|---|---|---|
| `pvesh get/create/set/delete <path>` | the whole API from the shell, the schema printed with `usage` | the same, on API v1 |
| `qm`, `pct` | short commands per guest type (`qm start 100`) | `hyperlite-admin vm start web1` |
| Authentication | root on the node is `root@pam`, no ticket to type | root on the node gets a ticket from the daemon (4.3) |
| (vSphere) `esxcli` | one command, namespaces (`esxcli network ip interface list`), the whole help from one place | one command, `hyperlite-admin <group> <action>` |
| (vSphere) Lockdown mode | the host refuses root's direct access; only named accounts and exception users | `admin_cli_lockdown`: root's implicit identity refused, a named account's token still works (4.3) |
| Output | `--output-format json|json-pretty|text|yaml` | `--json`, a table otherwise |
| Long operations | waits for the task, prints its log | the same (`WaitTask` of `cli/hyperlite`) |

## 3. Where we are

- **API v1**: 264 routes, every one under `/api/v1`, errors `{"detail", "code"}`, long operations answer a task.
- **`cli/hyperlite`** (Go, MPL 2.0): the client library of the workstation client and of the Terraform provider: TLS
  with a pinned certificate, v1 errors, `WaitTask` with the task's last log lines. `installer/build-deb.sh` already builds
  Go (`installer/build-cli.sh`), so the package needs no new tool.
- **Tickets** (`app/core/tickets.py`): sessions are Ed25519 tokens signed by `hyperlite-daemon` with the node's key,
  only after the daemon checked a proof itself (`app/core/sign_in.py`); the API verifies them with the public keys.
- **`hyperlite-daemon`** (`app/daemon/server.py`): a Unix socket, `/run/hyperlite/daemon.sock`, 0660, that knows the
  peer's uid from the kernel (`SO_PEERCRED`) and accepts root and the API's user only.

## 4. Design

### 4.1 One binary, Go, in the package

`/usr/bin/hyperlite-admin`, built from `cli/admin/` on the `cli/hyperlite` library (so a fix to TLS, errors or the wait
for a task is made once for the three programs), shipped by the `.deb`. Root only: it says so and stops for any other
user.

### 4.2 Transport: the node's own HTTPS API

It calls `https://127.0.0.1:8000/api/v1`, pinning the node's certificate read from `/etc/hyperlite/local/hyperlite.crt`
(root only can read the key, anyone the certificate; a pin is exactly what `cli/hyperlite` already does for a
self-signed one). No new listener, no change to `hyperlite.service`: the requests go through the same middleware,
rights and audit as the dashboard's. The port comes from the unit's `--port` when an administrator changed it.

### 4.3 Identity: a ticket from the daemon, for root only

1. The CLI connects to the daemon's socket and asks `auth.local`.
2. The daemon answers only when the kernel says the peer's uid is **0** (not the API's user, once #511 drops root): a
   session ticket, signed as any other, **5 minutes**, for `root@<node>`, with a claim `via: "hyperlite-admin"`
   and the peer's pid.
3. The CLI sends it as `Authorization: Bearer`. A run longer than the ticket (waiting for a long task) asks for a new
   one; nothing is written to disk.

Why not a token stored on the node: a file root can read is a secret to rotate and to leak in a backup. Why not a
plain Unix socket on the API with `SO_PEERCRED`: uvicorn would need a second listener and every route a second way to
authenticate; the ticket keeps one way.

`root@<node>` is no row of `users`: the API knows it from the ticket alone, with the global role `admin`, and accepts it
only in a ticket signed by that node's own key (the `kid` of a key the node published), so a ticket for `root@n1` signed
by n2 is refused. A request it sends to another member (`--node n2`) carries a peer ticket (`app/core/peer_auth.py`),
which n2 accepts for `root@n1` only when n1 signed it: root on one member acts on the cluster as `pvesh` does on
Proxmox VE, under its own node's name in each member's audit.

A named account instead of root: `--token-file FILE` (or `HYPERLITE_TOKEN`) uses that account's API token, its own
rights and its own name in the audit, as an administrator does from a workstation.

**Lockdown**, as ESXi's lockdown mode: an administrator can turn on `admin_cli_lockdown` (a key of the replicated
`app_settings`, no new column; off by default, set from the security page). Then the daemon refuses `auth.local` on
every member and says why; root on a node must use a named account's token. It protects an organization that wants
every action under a person's name, at the price of `hyperlite-admin` with no token.

Until #511 step 4, the API itself runs as root, so it could ask `auth.local` too: it gains nothing it did not have (it
reads the signing key today). After step 4 the uid check is what keeps the API out.

### 4.4 Commands

Two layers, both on the same calls:

- **Generic, as `pvesh`**: `hyperlite-admin get|post|put|patch|delete <path> [key=value ...] [--data JSON]`, any
  route of API v1; `hyperlite-admin api <path>` prints the route's parameters from the server's own OpenAPI
  (`/openapi.json`), so nothing in the CLI goes stale when the API grows.
- **Short commands** for daily work, each a thin wrapper of one or two routes:

| Group | Commands |
|---|---|
| `vm` | `list`, `show`, `start`, `stop [--force]`, `reboot`, `suspend`, `resume`, `migrate --to`, `snapshot list/create/rollback/delete`, `clone`, `delete` |
| `ct` | `list`, `show`, `start`, `stop`, `delete`, `snapshot ...` |
| `storage` | `list`, `show`, `volumes` |
| `network` | `list`, `show`, `start`, `stop` |
| `backup` | `list`, `create <vm>`, `restore`, `verify` |
| `task` | `list`, `show`, `log`, `wait`, `cancel` |
| `cluster` | `status`, `nodes`, `maintenance on/off <node>`, `rolling-update` |
| `node` | `status`, `update check`, `update` (asks to confirm unless `--yes`; the same call as the dashboard's button) |
| `cluster` (setup) | `create`, `join`, `leave`, as `pvecm`: the same calls as the Cluster page |

A guest on another member is named with `--node` (the API's `?node=`): the member does the work, as from the dashboard.
`vm list --cluster` uses `?cluster=true`.

### 4.5 Output and exit codes

- A table by default; `--json` prints the API's answer as it is (a list keeps `{"items", "total"}`), for `jq`.
- A long operation waits for its task and prints its log as it comes (`--no-wait` prints the task id).
- Exit code: 0; 1 for an error of the API (its `code` and `detail` on stderr, `code` alone in `--json`); 2 for a
  usage error; 3 when a task failed; 4 for a timeout of `--timeout`.

### 4.6 Audit

Every action is the API's, so it is in the audit log as any other. The audit log keeps a user name, an action, a
resource and a result, no client: the name `root@<node>` already says it was the CLI and on which node, with no schema
change. With a named account's token (4.3), the entry carries that account's name, as from a workstation.

## 5. Files

| File | Change | Held by |
|---|---|---|
| `cli/admin/` (new) | the commands | nobody |
| `cli/hyperlite/` | additions only (a `Do` with query parameters, the OpenAPI read) | nobody |
| `app/daemon/ops/auth.py` | the `auth.local` operation | **#511** (`app/daemon/`): asked on #298 first |
| `app/core/sign_in.py` or `peer_auth.py` | the ticket for `root@<node>` | nobody |
| `app/core/security.py` | accept `root@<node>` (no row in `users`) | nobody |
| `dashboard/src/next/pages/SecurityPage.jsx`, `i18n/en.js`, `fr.js` | the lockdown switch | `i18n/*.js`: **#426** |
| `installer/build-cli.sh`, `installer/build-deb.sh` | build and ship `hyperlite-admin` | maintainers (onboarding: coordinate first) |
| `docs/user-guide.md`, `docs/proxmox-parity.json` | the commands; the row of #516 | `docs/proxmox-parity.json`: **#426** |

No table, no column of the replicated configuration changes. No route of the API changes; the lockdown is a
key of `app_settings`, set through the existing settings route if one fits, else one addition to API v1.

## 6. Steps (one pull request each)

1. **Identity**: `auth.local` in the daemon, `root@<node>` accepted by the API, its audit, the lockdown setting; tests
   (uid 0 gets a ticket, any other uid is refused, a ticket of 5 minutes expires, the audit names `root@<node>`, lockdown
   refuses it and a named token still works).
2. **The generic layer**: `get/post/put/patch/delete`, `api`, `--json`, the wait for tasks, exit codes; Go tests against
   a test server, and a test against a real API started by the test suite.
3. **Short commands**: `vm`, `ct`, `task`, then `storage`, `network`, `backup`, `cluster`, `node`.
4. **The package**: built and shipped; checked on the lab (`lab/tests`): on node 1, `hyperlite-admin vm list --cluster`
   lists the three members' VMs, `vm start --node n2` starts a VM on node 2, and the audit of node 2 names it.

## 7. Risks

| Risk | Mitigation |
|---|---|
| A root shell becomes a Hyperlite administrator without a second factor | It is root on the node already (it reads the database and the keys); Proxmox VE's `root@pam` is the same. The audit names it |
| A ticket leaks from a running command | 5 minutes, never written to disk, only in the process's memory |
| The API's process asks for a root ticket after #511 drops root | The daemon checks uid 0 from the kernel, not a claim of the caller |
| The short commands drift from the API | They are thin wrappers; the generic layer and `api` cover the rest from the server's own schema |

## 8. Choices (left by the maintainer to the best of vSphere and Proxmox VE, 2026-10-23)

| Question | vSphere | Proxmox VE | Chosen |
|---|---|---|---|
| Who is root in the audit | `root` on ESXi; named SSO accounts on vCenter; lockdown mode to refuse root | `root@pam`, always allowed | **`root@<node>`**, an administrator with no password that cannot sign in from the web, as `root@pam`; **plus lockdown** as ESXi, off by default; a named account through its token (4.3) |
| Language | `esxcli`: Python on the host | Perl, in-process (`pvesh` takes about half a second to start) | **Go** on `cli/hyperlite`: starts at once, one static binary, the same client library as the workstation client and the Terraform provider; the package already builds Go |
| Name | one command with namespaces (`esxcli`) | five (`qm`, `pct`, `pvesh`, `pvecm`, `pvesm`) | **one, `hyperlite-admin`**, with groups: the whole help from one place, one binary to ship, as the issue named it |
| What it may do | `esxcli software` updates a host, maintenance mode | `pveupgrade`, `pvecm create/add` | **everything the API does**, the update and the cluster's setup included, with a confirmation for the update; the generic layer could reach those routes anyway, so refusing them in short commands would protect nothing |

Who updates production is a rule between people (`docs/onboarding.md`), not something the CLI changes: it makes the
same call as the dashboard's button, under the same lock (HTTP 409 when another update runs).
