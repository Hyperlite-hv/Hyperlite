# Workbench: a terminal in the console, for every account, within its rights

Status: design (#914); step 1 (the box, the daemon's operations, the sessions and their routes) implemented, not yet run
on a real host: the escape tests of section 14 run in a disposable VM (section 17, decision 5).

The console gains a `>_` button at the top right. It opens a panel docked at the bottom of every page: a terminal that
stays open while you move between pages, with the `hyperlite` client already signed in as you, `ssh` to your VMs
through Hyperlite, and the usual tools. An administrator also gets the node's root shell in the same panel. The name in
the interface is **Terminal** (section 17).

## 1. What a person does with it

- An operator who may restart the VMs of the pool `web` opens the Workbench and types `hyperlite vms`, then
  `hyperlite ssh web-1`: it works, with exactly the rights of their account, without installing anything on their own
  machine, from any browser.
- A team lead writes a small script in their Workbench home (`~/restart-web.sh`) and finds it again the next day.
- An administrator downloads a pool's Terraform export (governance, section 12) and runs `tofu plan` against it.
- An administrator fixes a node from a root shell in the same panel, while keeping the console page they were on.

What it is not: a way around permissions. Everything a Workbench session does to Hyperlite goes through the API, with
the session's own token, checked like any other request (and by hyperlite-daemon behind it). The shell itself runs in a
box that sees no secret of the node and reaches nothing on the network but the API.

## 2. What exists to build on

| Piece | Where | What it gives |
|---|---|---|
| Terminal sessions opened by the daemon, handed to the API as a descriptor | `app/daemon/ops/console.py` (`console.host_shell`), `app/core/console_sessions.py` (`open_pty`) | a PTY created by root, passed over the daemon's socket (`Handover`) |
| Browser bridge | `app/core/pty_session.py` (`open_session`, `bridge`), `app/routers/host.py` (`/host/terminal`) | single-use ticket, WebSocket, resize, audit and task per session |
| Web terminal | `dashboard/src/next/windows/LiveTerminal.jsx`, `dashboard/src/utils/loadXterm.js`, `lib/prefs.js` (`termOptions`) | xterm.js, fonts, reconnection with a fresh ticket |
| Workstation client | `cli/` (`hyperlite`: `login`, `vms`, `ssh`, `rdp`, `tunnel`, `open`), built for `linux-amd64` into `cli/dist`, shipped in the package | `HYPERLITE_SERVER`, `HYPERLITE_TOKEN`, `HYPERLITE_CONFIG`; pinned certificate fingerprint; `ssh` through the API's tunnel (`/vms/{name}/tunnel-ticket`, `vm.tunnel`) |
| Tokens that act with fewer rights | `app/core/api_tokens.py`, `api_token_scopes` (access 2, section 7) | a token tied to a role on a perimeter, never wider than its owner |
| Transient units started by the daemon | `app/daemon/ops/hyperlite.py` (`hyperlite-update-job-*` through `systemd-run`) | the pattern for a process that outlives the API and is supervised by systemd |
| Sign-in rules, exclusions, journal, tasks | `app/core/signin_rules.py`, `permissions.excluded`, `audit.log_action`, `tasks` | the governance a session must follow and leave traces in |

The API runs as `www-data` and cannot start processes as anyone else: the box is started by hyperlite-daemon (root),
as the root shell is.

## 3. The box: options

| | A. Hardened transient unit (`systemd-run`) | B. Container from an image (`systemd-nspawn`) | C. An LXC container per account | D. A small VM per account |
|---|---|---|---|---|
| Root file system | the host's, read-only, sensitive paths hidden | its own (Debian minbase + tools), read-only | its own | its own |
| Start time | ~50 ms | ~300 ms | ~1 s | 10-30 s |
| Memory at rest | a shell (~5 MB) | a shell + init (~15 MB) | ~30 MB | 256 MB+ |
| Tools | the host's (ssh, curl, git, python3, tmux) + the shipped `hyperlite` | whatever the image holds; can be extended per site | as B | anything |
| Isolation | kernel namespaces + seccomp + no privileges + private network; same kernel | same, plus a separate file system; user namespace | as B, managed as a guest | a separate kernel |
| What ships | nothing new (two small dependencies) | an image to build, sign and update (~150 MB) | an image, and a guest per account to manage | a template, disks per account |
| Fits Hyperlite today | yes: same pattern as the update job | needs an image pipeline (the image factory could make it) | mixes users' shells with real workloads | too heavy for a terminal |

**Decision proposed: A for the first version**, with every hardening systemd 257 (Debian 13) offers, a private network
and the sensitive paths hidden. B is kept as a later option ("custom image") for sites that want their own tools in the
box; the session lifecycle, identity and interface below do not change with it.

## 4. Architecture

```
 browser (xterm.js, docked panel)
    │  WSS /api/v1/workbench/sessions/{id}/attach?ticket=…   (single-use ticket, like /host/terminal)
    ▼
 API (www-data) ── pty_session.bridge ──┐ holds the PTY master the daemon handed over
    │                                   │
    │ privileged.run("workbench.open")  │
    ▼                                   │
 hyperlite-daemon (root)                │
    ├─ mints nothing: the API made the session's token and passes it in a sealed file (6)
    ├─ mounts the account's home image  (9)
    ├─ systemd-run → hyperlite-workbench-<sid>.service   (DynamicUser, PrivateNetwork, …, section 6)
    │      └─ tmux new-session -A -s main  ── bash -l
    │      └─ hyperlite-api-relay  127.0.0.1:8443 (inside) ⇄ /run/hyperlite/workbench/<sid>/api.sock
    ├─ relay (outside): api.sock ⇄ 127.0.0.1:8000 (the API, TLS end to end)
    └─ opens a PTY on `tmux -S <the session's socket> attach`, run as the session's uid (setpriv), and hands it over
```

- The shell runs under `tmux` inside the unit: a dropped WebSocket, a browser reload or an API restart (an update)
  re-attaches to the same shell; only the end of the unit ends it.
- The box's only network is its own loopback. A relay inside listens on `127.0.0.1:8443` and forwards to a UNIX socket
  bound into the box; the daemon's relay on the other side connects that socket to the API on `127.0.0.1:8000`. TLS
  runs end to end, from the client in the box to the API, with the node's certificate pinned in the client's config. The
  box cannot reach libvirt, hyperlite-cfs, the other members, the VMs' networks or the internet directly: `hyperlite
  ssh` reaches a VM through the API's tunnel, with `vm.tunnel`.
- One unit per session, named after the session id, so systemd accounts its CPU, memory and processes and ends them all
  together.

## 5. A session, from start to end

1. **Open** (`POST /workbench/sessions`): the API checks the account may use the Workbench (section 13), that it is
   under its limits (sessions per account, per node), and the sign-in rules **with the browser's address**. It creates
   the session row and its token (section 7), then asks the daemon `workbench.open` with the session id, the account
   name, the token's sealed file and the limits.
2. **Daemon**: mounts the account's home (section 9), writes the client's config into the session's runtime directory
   (server `https://127.0.0.1:8443`, the node certificate's fingerprint, the token), starts the transient unit and the
   outside relay, waits for tmux, opens the PTY and hands it over. A failure at any step undoes the previous ones.
   Attaching needs no entry into the box's namespaces: the tmux client talks to the tmux server through its socket in
   the session's runtime directory, as the session's uid; the shells stay inside the box. (`PrivateUsers=yes` maps the
   session's uid to itself, so the server accepts that client: to be verified on a lab node in step 1.)
3. **Attach** (`POST /workbench/sessions/{id}/ticket`, then the WebSocket): a single-use ticket for 30 s, as for the
   other terminals. Re-attaching after a drop takes a new ticket for the same session.
4. **Idle and limits**: no input and no output for 30 minutes ends the session (configurable); a session never lasts
   more than 8 hours (`RuntimeMaxSec`, configurable). The hours of a sign-in rule that close end the session too: the
   scheduler checks open sessions every minute.
5. **Close** (the `exit` of the shell, the panel's "end session", the limits, the account disabled, an administrator
   from the sessions list): the token is revoked, the unit stopped (`systemctl stop`, then `kill` after 10 s), the relay
   stopped, the home unmounted, the session row closed, the task finished and journaled.
6. **The service restarts** (an update): the units are independent of the API and of the daemon; at start the API
   lists `hyperlite-workbench-*` units and re-adopts the ones whose session row is open (re-attach), ends the others.
   A node reboot ends every session (the rows are closed at start).

## 6. The box, property by property

Started with `systemd-run --unit=hyperlite-workbench-<sid> --property=…`:

| Property | Value | Why |
|---|---|---|
| `DynamicUser=yes` | a uid made for the session | no account on the host, no file owned outside the session |
| `User=` | `hl-wb-<sid>` | the name the journal shows |
| `PrivateUsers=yes` | | root inside the namespace is nobody outside |
| `NoNewPrivileges=yes`, `CapabilityBoundingSet=`, `AmbientCapabilities=` | none | setuid binaries (sudo, ping) give nothing |
| `ProtectSystem=strict`, `ProtectHome=yes` | | the whole host read-only, `/home` and `/root` hidden |
| `InaccessiblePaths=` | `/etc/hyperlite /var/lib/hyperlite /var/lib/libvirt /etc/libvirt /run/libvirt /run/hyperlite /var/log /var/backups /srv /mnt /media /etc/ssh/ssh_host_* /etc/shadow /etc/gshadow` | the node's secrets, databases, disks, sockets and logs are not there at all |
| `BindPaths=` | the account's home on `/home/workbench`, the session's runtime dir on `/run/workbench` | the only writable places, with `PrivateTmp=yes` |
| `PrivateNetwork=yes` | | only its own loopback (section 4) |
| `RestrictAddressFamilies=` | `AF_UNIX AF_INET AF_INET6` | no netlink, no raw sockets, no packet sockets |
| `PrivateDevices=yes`, `DevicePolicy=closed` | | no disk, no `/dev/kvm`, no `/dev/mem` |
| `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname` | yes | nothing of the kernel or the host to change |
| `ProtectProc=invisible`, `ProcSubset=pid` | | the host's processes are not visible, nor `/proc/sys` |
| `RestrictNamespaces=yes`, `LockPersonality=yes`, `RestrictRealtime=yes`, `RestrictSUIDSGID=yes`, `MemoryDenyWriteExecute=no` | | no nested containers; MDWE stays off (interpreters with a JIT) |
| `SystemCallFilter=` | `@system-service`, minus `@privileged @resources @mount @swap @reboot @raw-io @module @debug` | a reduced kernel surface |
| `SystemCallArchitectures=native` | | |
| `MemoryMax=`, `MemorySwapMax=0`, `CPUQuota=`, `TasksMax=`, `IOWeight=` | 512M, 0, 50 %, 128, 50 (configurable) | a fork bomb or a memory hog stays in its box |
| `RuntimeMaxSec=` | 8 h (configurable) | |
| `UMask=0077`, `KeyringMode=private` | | |
| `Environment=` | `HOME=/home/workbench`, `HYPERLITE_CONFIG=/run/workbench/hyperlite.json`, `TERM=xterm-256color`, `LANG`, `PS1` | |

What the person sees: a normal Debian shell, `/usr` and `/etc` readable (as any user of the host would), their home
writable, `/tmp` private, no host process, no network but the API. Two dependencies join the package: `tmux` (already
present on most nodes) and `jq`.

## 7. Identity and rights

- **The session's token**: an API token of kind `workbench`, owned by the account, ending with the session (at most
  its `RuntimeMaxSec` plus a minute), revoked at close. It is never shown in the interface; it lives in the session's
  runtime directory (0600, owned by the session's uid). A token cannot make other tokens (`POST /auth/tokens` needs a
  signed-in session), so a stolen one dies with the session.
- **Acting with fewer rights**: the panel may open a session "as" a role on a perimeter (a delegated token, access 2,
  section 7): the Workbench of a delegate who wants to try a script with Observer rights only.
- **Every request is a normal request**: permissions, exclusions, protections, guardrails and the daemon's own checks
  apply. Nothing in the box talks to the daemon.
- **Sign-in rules**: a request from the box arrives from `127.0.0.1` (the relay). A token of kind `workbench` is checked
  against the rules with the address the session was opened from (kept in the session row), and the scheduler ends a
  session when its rules stop allowing it (hours, days). A rule requiring a second factor is met by the session that
  opened the Workbench.
- **Journal**: session opened, attached, ended (with why), and every API call the session makes appears under the
  account with the client `workbench/<sid>`. Opening a session is a task (as the root shell is), visible in Activity.

## 8. The tools in the box

- `hyperlite` (the shipped `cli/dist/linux-amd64/hyperlite`, put on the `PATH` from `/usr/lib/hyperlite/cli/dist`),
  configured by the daemon: `hyperlite vms`, `hyperlite ssh <vm>`, `hyperlite open <vm>`. New subcommands that make the
  Workbench useful, as part of this work: `hyperlite api GET /vms` (any route, JSON out, `jq`-friendly), `hyperlite
  export pool <name>` (the governance export).
- The host's own: `bash`, `ssh` (through `hyperlite ssh`), `curl`, `git`, `python3`, `tmux`, `jq`, `less`, `nano`,
  `vim` when present.
- OpenTofu is not packaged by Debian: the Workbench offers to install a pinned, checksummed release into the account's
  home on first use (`hyperlite tools install tofu`), downloaded by the daemon (the box has no internet).
- A short welcome text (motd) with the account's rights in one line (`GET /access/me`) and three examples.

## 9. The home that stays

- Each account gets an image file `/var/lib/hyperlite/workbench/homes/<account>.img` (ext4, 1 GiB by default,
  configurable), made at first use, checked (`e2fsck -p`) and loop-mounted `nodev,nosuid` by the daemon at session
  start (scripts may run from it), unmounted at the end. The image's size is the quota; the
  account sees "No space left" at the limit, never fills the node.
- Two sessions of the same account share the home (mounted once, counted).
- An image unused for 90 days is deleted (journaled); an account deleted loses it at once.
- Per node: an account that opens the Workbench on another member of the cluster gets that member's home. A later
  version may keep homes on a shared storage pool; the first one says so in the panel.

## 10. The other tabs of the panel

- **Root shell** (administrators): the existing `console.host_shell`, in a panel tab instead of the node's page.
- **SSH to a VM** (`vm.console`): the existing VM terminal (`/vms/{name}/terminal-ticket`), opened in a tab from any
  VM's menu ("Open in the Workbench").
- **Container terminal**: the existing one, likewise.
These reuse their routes and tickets as they are; only where they are shown changes.

## 11. The interface

- **Button**: `>_` in the top bar, left of the notifications, with the shortcut ``Ctrl+` `` (and `g t` in the
  keyboard map). Hidden for an account the Workbench is closed to.
- **Docked panel** at the bottom of the workspace, above the status line: a drag handle to resize (height kept per
  browser), maximize, minimize to a strip, pop out into its own window (the `windows/` pages), close. It survives page
  changes (it lives in the layout, not in a page) and comes back after a reload, re-attached.
- **Tabs**: "Workbench" (several, numbered), "Root · node", "SSH · vm", with a `+` menu. A tab whose session ended says
  why (idle, limit, rule, closed by an administrator) and offers a new one.
- **Status line** of a tab: the node, the rights it acts with ("as you" or the delegated role), the time left.
- **Files**: drop a file onto the panel to upload it into the home; `hyperlite download <path>` (or a menu) to get one
  back; both through the API (`/workbench/files`), size-limited, journaled.
- **Terminal**: the console's terminal preferences (font, size, theme), copy on select, paste with the browser's own
  shortcut, links clickable, search in the scrollback.
- **Phone**: the panel opens full screen with a key row (Esc, Tab, Ctrl, arrows).

## 12. API, daemon and storage

Routes (prefix `/api/v1`):

| Route | Who | What |
|---|---|---|
| `GET /workbench` | any account | settings that concern the caller, its open sessions, its home's size |
| `POST /workbench/sessions` | allowed accounts | open a session (`{"as": {role, resource_type, resource_id}?, "cols", "rows"}`) |
| `POST /workbench/sessions/{id}/ticket` | its owner | a single-use attach ticket |
| `WS /workbench/sessions/{id}/attach` | the ticket | the terminal |
| `DELETE /workbench/sessions/{id}` | its owner, an administrator | end it |
| `GET /workbench/sessions` | administrators | every open session of the node (account, since, idle, address) |
| `PUT /workbench/settings` | administrators | section 13 |
| `POST /workbench/files`, `GET /workbench/files/{path}` | its owner, during a session | upload / download in the home |
| `DELETE /workbench/homes/{account}` | administrators, the account itself | delete a home |

Daemon operations (`app/daemon/ops/workbench.py`), each checked like the others (`needs`): `workbench.open` (the
account's own, or an administrator), `workbench.attach` (returns a PTY on `tmux attach`), `workbench.close`,
`workbench.list` (the units), `workbench.home_delete`, `workbench.file_put`, `workbench.file_get`. The daemon builds
the unit's properties itself from fixed code and the settings: the API never passes a property, a path or a command.

Storage: `workbench_sessions` (per node: id, account, opened_at, from_address, token_id, as_role, state, ended_why),
settings in `app_settings` (replicated, the same for every node).

## 13. Settings and governance

- **Who may use it**: a new area privilege `workbench.use` (held on the cluster or a node), in the roles Steward,
  Builder and Maintainer; a global switch "Workbench for everyone signed in" (off by default). Administrators always.
- **Limits**: sessions per account (2), per node (20), memory, CPU, processes, idle time, longest session, home size.
- **Network**: "API only" (the default and the only choice of the first version). A later option may let a box reach
  the internet through the node's outbound proxy, never the node's own networks.
- **Recording** (off by default): keep each session's typescript (input and output) for N days, readable by
  administrators only, said in the panel to the person ("this session is recorded"). Useful for audits; a privacy choice
  the site makes.
- **Access review**: open sessions and the last use of each home appear in the review; an account's home goes when the
  account goes.

## 14. Threats and how each is closed

| Threat | Closed by | Checked by (test) |
|---|---|---|
| Read the node's secrets (keys, database, tokens) | `InaccessiblePaths`, `ProtectSystem=strict` | `cat /etc/hyperlite/priv/env`, `ls /var/lib/hyperlite` fail |
| Become root (sudo, setuid, capabilities) | `NoNewPrivileges`, no capabilities, `PrivateUsers` | `sudo -n true`, `ping`, `unshare -r` fail |
| Reach libvirt, cfs, the cluster, the VMs' networks | `PrivateNetwork`, sockets hidden | `curl 127.0.0.1:16509`, `ssh 10.0.0.x` fail; only `:8443` answers |
| Escape through the kernel | seccomp filter, no namespaces, no devices, no modules | the filter's list checked by a unit test; `mount`, `bpf`, `keyctl` fail |
| Starve the node | cgroup limits, `TasksMax`, home image size | a fork bomb and `stress` stay in their box; the node's load stays |
| Read another account's files | a uid per session, homes mounted only into their own box | two sessions cannot see each other's `/home` or processes |
| Use the token elsewhere | short-lived, revoked at close, only in the box; same rights as the account anyway | a revoked token is refused |
| Bypass sign-in rules from the box | rules checked with the opening address, sessions ended when they stop allowing | a rule's hours ending closes the session |
| Hide what was done | journal of every API call, optional recording | the journal lists the session's calls |

These tests run on a lab node (an end-to-end script, `lab/workbench-escape.sh`), never on a production node.

## 15. Cluster, updates and failures

- A session lives on the member that served the browser; it acts on the whole cluster through that member's API (which
  forwards as it already does).
- An update restarts the API and the daemon: sessions keep running (their units are their own) and re-attach.
- The daemon down: no new session; open ones keep running, re-attach once it is back.
- The node low on memory: the boxes are the first candidates (`OOMScoreAdjust=500`), never the API or the daemon.
- A home image damaged: `e2fsck -p` repairs what it can; otherwise the session opens without the home and says so.

## 16. Steps

| Step | Content | Size |
|---|---|---|
| 1 | Daemon: the unit's properties (a pure function, unit-tested property by property), open/attach/close/list, the relays, the home images; `workbench_sessions`; routes to open, attach and end; the session token; sign-in rule handling; re-adoption at start | large |
| 2 | Interface: top-bar button, docked panel with tabs, re-attach, status line, the Workbench tab; the root shell and SSH tabs moved into it | medium |
| 3 | `hyperlite api`, `hyperlite export`, `hyperlite tools install tofu`, the welcome text; files up and down | medium |
| 4 | Settings page (who, limits, recording), sessions list for administrators, access review entries, `workbench.use` | small |
| 5 | The escape test script on a lab node, its findings fixed; documentation for users | small |
| later | Custom image (option B), internet through a proxy, homes on shared storage | |

Each step ships alone; the panel shows only what exists.

## 17. Decisions (Antho, 2026-11-14)

1. **Name**: "Terminal" in the interface, in English and French. The code, routes and units keep the identifier
   `workbench` (`/workbench/…`, `hyperlite-workbench-<sid>`), apart from the existing `/host/terminal` and
   `/vms/{name}/terminal-ticket`.
2. **Who by default**: administrators, and the privilege `workbench.use` in the roles Steward, Builder and Maintainer;
   a switch opens it to every account signed in.
3. **Recording**: offered, off by default, announced in the panel when on.
4. **OpenTofu**: offered as an install on demand (a pinned, checksummed release, downloaded by the node, installed into
   the account's home).
5. **Escape tests**: in a disposable VM on antho (Debian 13 with Hyperlite installed inside), made for the tests and
   deleted after; the tests run inside that VM, never on antho itself.
