# Design: privilege separation, a non-root API and a root daemon (#511)

Status: **accepted** (Antho, 2026-10-21, section 8). Target: 1.1.

The decision itself is older: on 2026-10-01 the maintainers chose Proxmox VE's service layout, with
`hyperlite-proxy` (the API, unprivileged) and `hyperlite-daemon` (the privileged actions, root, local socket)
(`docs/design/control-plane-v2-migration.md`, section 15.2.1). This document says how to get there from today's code,
in steps that each ship on their own.

## 1. Where we are (measured on test, 2026-10-21)

- One process, `uvicorn app.main:app`, **root**, listens on `0.0.0.0:8000` (TLS). It serves 321 HTTP routes, 6
  WebSockets (VM consoles, the host shell, container terminals), the dashboard, and runs its background work in the same process (31 threads on a node, measured).
- **75 of the 206 Python files** do something that needs root: external commands (150 call sites, 39 programs:
  `qemu-img`, `ssh`/`scp` to the other nodes as root, `systemctl`, `ip`, `iptables`, `zfs`/`zpool`, `iscsiadm`,
  `apt-get`/`dpkg`, `debootstrap`/`chroot`, `modprobe`, `certbot`…), the libvirt system connection (`qemu:///system`),
  about 60 system paths (`/etc/…`, `/var/lib/libvirt/…`, `/dev/watchdog`, `/root/.ssh/…`), and a root PTY (the host
  shell). They hold about 26,000 lines.
- Code and data live under `/root/hyperlite` (the database, TLS key, SSH keys, uploaded ISOs).

**The risk**: any flaw in any route (parsing, an injection, a path check missed) runs as root on the node, and through
its SSH keys, as root on every other node of the cluster.

## 2. Goal

1. The process that parses HTTP from the network has **no root rights** and cannot become root: user `hyperlite`,
   `NoNewPrivileges`, no capabilities, a read-only system.
2. Everything that needs root goes through **`hyperlite-daemon`**: root, a Unix socket only (`/run/hyperlite/daemon.sock`,
   `0660 root:hyperlite`), a **fixed list of operations**, each with typed and checked arguments. No "run this
   command", no "write this path".
3. Nothing changes for users: same dashboard, same API v1, same features.

What it does not claim: an administrator of Hyperlite can still do root things *through the allowed operations* (create
a VM with a host disk, open the host shell). The gain is that a **flaw** no longer gives root; using the product as
designed does what it always did.

## 3. Options considered

| Option | What it gives | Why not alone |
|---|---|---|
| **A. systemd hardening of today's process** (`ProtectSystem`, `ProtectHome`, `CapabilityBoundingSet`, `SystemCallFilter`…) | Days of work; limits what a flaw can reach (no kernel modules, no `/boot`, fewer capabilities) | Still root: libvirt, `/etc`, SSH to the peers stay reachable |
| **B. sudo or polkit rules per command** | No new daemon | 39 programs with free arguments: each rule is a hole (`qemu-img` writes anywhere, `ssh` runs anything) |
| **C. Proxmox's layout: proxy + daemon** (decided) | The network-facing code has no rights; the root code is small and audited | The biggest: every privileged call site moves behind an operation |

**Proposed: A first, then C**, A as the safety net while C is built, and kept afterwards on both services.

## 4. Target

```
browser ──HTTPS──▶ hyperlite-proxy (user hyperlite)          hyperlite-daemon (root)
                   FastAPI: auth, permissions, validation ──▶ /run/hyperlite/daemon.sock
                   reads: database, hyperlite-cfs (via daemon) operations: vm.define, disk.create, net.apply,
                   libvirt read-only                          storage.mount, host.apt, shell.open, peer.call…
```

- **The proxy** keeps everything that is logic: routes, permissions (it decides *who* may), the database
  (`/var/lib/hyperlite/hyperlite.db`, owned by `hyperlite`), the dashboard, notifications, the API v1 contract. It reads
  libvirt through a **read-only** connection (`virsh -r`, the `libvirt-sock-ro` socket, open to every local user by default; checked as `nobody` on a node):
  listing VMs, states and stats needs no root.
- **The daemon** does only what needs root, and checks again what it is asked: names against the same validators, paths
  with `safe_child()` inside their fixed bases, no shell. Each call is logged with the user the proxy names. It holds
  the SSH keys to the other nodes and the hyperlite-cfs socket.
- **Protocol**: one JSON request per connection line, `{"op": "disk.create", "args": {...}, "user": "alice",
  "request_id": "..."}`, answered `{"ok": true, "result": ...}` or `{"error": code, "detail": ...}` (built with the
  daemon's own kinds instead, which the API turns into its codes: section 13).
  Long operations (a disk copy, an apt run) answer at once with a task id, as today. The peer credentials of the
  connection (`SO_PEERCRED`) must be the `hyperlite` user.
- **Consoles and the host shell**: the daemon opens the PTY or the VNC socket and passes the file descriptor to the
  proxy (`SCM_RIGHTS`); the proxy streams it to the browser. The shell stays administrator-only, decided in the proxy.

## 5. Steps (each one ships, tests and lab run included)

| Step | Content | Size (rough) |
|---|---|---|
| **0. Hardening** | `hyperlite.service`: `ProtectSystem=strict` with the exact writable paths, `ProtectHome=read-only` except `/root/hyperlite`, `PrivateTmp`, `ProtectKernelModules` (modprobe moves to a helper unit), `ProtectKernelTunables`, `RestrictSUIDSGID`, `LockPersonality`, `RestrictRealtime`, a `SystemCallFilter=@system-service`; a test that each listed path is really written somewhere, and the e2e suite on the hardened unit | 1 week |
| **1. Layout** | Data to `/var/lib/hyperlite`, code to `/usr/lib/hyperlite`, user `hyperlite`; the update moves `/root/hyperlite` once (a symlink kept for scripts) | 1 week |
| **2. The daemon, empty** | `hyperlite-daemon` service, socket, protocol, peer check, audit, the operation registry, tests (refused peer, unknown op, bad args) | 1 week |
| **3. Domains, one PR each** | host system (apt, systemctl, `/etc` files) → network (ip, iptables, libvirt networks) → storage (zfs, iscsi, mounts) → disks (qemu-img, images) → VMs (define, start, migrate) → containers (debootstrap, chroot) → cluster (SSH to peers, corosync, cfs socket) → consoles and shell (fd passing) | 6–8 weeks |
| **4. Drop root** | `User=hyperlite` on the proxy; a test, on the lab, that the proxy cannot write `/etc`, read `/root`, load a module or reach the cfs socket; the whole e2e suite | 1 week |

Each step of 3 moves call sites behind operations without changing behaviour; the proxy keeps working as root until
step 4, so the move can be done domain by domain and released between them.

## 6. Risks

- **Size and regressions**: 150 call sites. Each domain PR runs the full suite and the lab; a domain is moved whole.
- **Performance**: a Unix-socket round trip is about 0.1 ms; the VM list (the hottest path) reads libvirt read-only in
  the proxy, so it does not cross the socket.
- **Updates**: the step-1 move runs inside the update watchdog; a failure puts the previous layout back.
- **Clusters with mixed versions** during a rolling update: the daemon is local to a node, nothing crosses nodes that
  did not before.

## 7. Questions for the maintainer

1. **Step 0 first** (systemd hardening, about a week, real gain at once) **then the split**, as proposed?
2. **libvirt in the proxy**: read-only only, every change through the daemon (proposed, the stronger choice), or full
   libvirt access for the `hyperlite` group (faster; but libvirt access is root-equivalent, a VM can be given any host
   disk)?
3. **The host shell** (a root terminal in the browser): kept, through the daemon, administrators only (proposed), or
   removed?
4. **The layout move** (`/root/hyperlite` → `/var/lib/hyperlite`, `/usr/lib/hyperlite`): accepted, with a symlink left
   for the scripts that use the old path?

## 8. Decisions (Antho, 2026-10-21)

1. ~~Step 0 first~~: dropped after its analysis (the service writes `/etc`, `/root/.ssh` and `/usr` for features of the
   dashboard, so a sandbox would either break them or leave most open): the full split straight away.
2. **libvirt read-only in the proxy**; every change goes through the daemon.
3. **The host shell stays**, opened by the daemon, administrators only, audited.
4. **The layout move, without a link**: `/root/hyperlite` moves to `/var/lib/hyperlite` (data) and `/usr/lib/hyperlite`
   (code) and is not left behind as a symlink; every script, unit and document that names the old path changes in the
   same step.

## 9. Step 1 in detail: moving out of /root/hyperlite

Modelled on Proxmox VE's layout (Antho, 2026-10-21: "everything as Proxmox does it"):

| What | Proxmox VE | Hyperlite from 1.1 | Up to 1.0 |
|---|---|---|---|
| code, interface | `/usr/share/perl5/PVE`, `/usr/share/pve-manager` | `/usr/lib/hyperlite`, `/usr/share/hyperlite/www` | `/root/hyperlite` |
| cluster configuration | `/etc/pve` (pmxcfs) | `/etc/hyperlite` (hyperlite-cfs, mounted as files later: #525) | database + hyperlite-cfs |
| cluster secrets | `/etc/pve/priv` | hyperlite-cfs `/priv` | `.env` |
| node secrets, SSH keys | `/etc/pve/priv`, `/root/.ssh` | `/etc/hyperlite/priv` (`env`, `ssh/`) | `.env`, `data/ssh` |
| node TLS | `/etc/pve/local/pve-ssl.*` | `/etc/hyperlite/local` | `data/tls` |
| ISO images | `/var/lib/vz/template/iso` | `/var/lib/hyperlite/template/iso` | `data/isos` |
| VM templates | (VMs) | `/var/lib/hyperlite/template/vm` | `data/templates` |
| backups, exports | `/var/lib/vz/dump` | `/var/lib/hyperlite/dump`, `dump/export`, `dump/container` | `data/backups`, `data/vm-exports`, `/root/hyperlite-container-backups` |
| disks to import | `/var/lib/vz/import` | `/var/lib/hyperlite/import` | `data/imported-disks` |
| database, k3s, tools, update backups | `/var/lib/pve-cluster`, `/var/lib/pve-manager` | `/var/lib/hyperlite` (`hyperlite.db`, `k3s`, `bin`, `tmp`, `update-backups`, `config-copy-staging`) | `/root/hyperlite`, `/root/hyperlite-backups` |
| logs | `/var/log/pve`, `/var/log/pveproxy` | `/var/log/hyperlite` | the same |
| the API's account | `www-data` (pveproxy) | `www-data` | root |

`/var/lib/hyperlite/config-copy`, where a node receives a controller's configuration, stays where it is in both
layouts. `scripts/migrate_layout.sh` holds the exact list of moves.

**The trap.** The update is run by the version already installed. Up to 1.0.1 it runs apt, then starts its watchdog
from `/root/hyperlite/scripts/update_watchdog.sh`, which the new package would no longer ship, so dpkg removes it
before it starts; and the watchdog's rollback restores an archive of `/root/hyperlite`, which the new unit no longer
uses. A version that moves the files cannot be installed safely by one that does not know about the move.

**Two releases, as 2026.10.19.0616 was the step to 1.0:**

1. **1.0.2, the bridge** (moves nothing):
   - the updater copies the watchdog to `/var/lib/hyperlite/update/` before apt and starts it from there;
   - the watchdog's rollback reinstalls the kept package of the previous version (`apt-get install --allow-downgrades`),
     instead of extracting an archive of `/root/hyperlite`: it works whatever the layout of either version;
   - its unit moves the files back to `/root/hyperlite` at start (`migrate-layout --back`) when it finds them in the new
     place, so a 1.1 that fails and is rolled back to 1.0.2 finds its data;
   - between nodes, an ISO library is looked for in both places.
2. **1.1.0, the move**:
   - its `preinst` refuses to install over a version older than 1.0.2 (apt stops before unpacking anything, the node
     keeps running, the message says to install 1.0.2 first);
   - its unit's `ExecStartPre` (`migrate-layout`, idempotent) moves every item with the service stopped: renames on the
     same file system, instant whatever their size; on any failure it moves back what it moved and the start fails,
     which the 1.0.2 watchdog turns into a rollback;
   - the return to a version that does not know the new layout is never offered (as below 1.0, `update._previous`).
     The return to 1.0.2 is offered: it moves the files back.

**What else names a path** (found while writing 1.1):

- **VMs with an ISO image inserted**: libvirt keeps the image's full path in the VM's definition. The move rewrites the
  persistent definitions whose image did move (read with `--security-info`, or the console's password would be lost);
  a running VM keeps the file it opened and reads the new path at its next start. Going back to 1.0.2, the package's
  `postrm` points them back before 1.0.2's `postinst` moves the files (`postrm` lives in dpkg's database: the code
  under `/usr/lib/hyperlite` may already be gone when it runs).
- **certbot**: each domain's renewal configuration keeps the path of the hook that installs a renewed certificate;
  rewritten both ways the same way.
- **Rows written up to 1.0**: each backup schedule stored the default directory in full (`/root/hyperlite/data/backups`),
  and these rows are replicated: rewriting them would send a node still on 1.0 (rolling update) to the directory of
  1.1. They are left as they are and read at the new place (`paths.current`), with the backups catalogue; from 1.1 the
  default directory is stored empty, which every version reads as its own.
- **A node already moved**: a VM migrated from a node still on 1.0 brings its ISO image to the same path, under
  `/root/hyperlite`. The move then only finishes what is left, never keeps the service from starting over an item
  found in both places, and leaves such an image where the VM reads it.
- **What 1.0 leaves**: the Python environment and the bytecode under `/root/hyperlite` are removed, then the empty
  directories; anything else (a file of the administrator's) stays, named in the service's log.
- **The return asked from the dashboard** ran apt from the service itself, still running: 1.0.2's package moved the
  database back under it, and it recreated an empty one at the new place (seen on the test VM). From 1.1 the watchdog
  does it, as for a failed update: service stopped, package installed again, service started.

Checked on the test VM with the real package (2026-10-21): 1.0.3 to 1.1, the return to 1.0.3 from the dashboard, a 1.1
that moves the files then fails to start installed over 1.0.3 and over 1.1 (back by itself in two minutes, by the kept
package and by the archive), the refusal over 1.0.1, a fresh installation, a removal and a reinstallation over the
kept data. Each time the secrets, keys, ISO image, backups, database rows and the VM's ISO path were the same.

Known limit, during a rolling update only: a node still on 1.0 that writes into a directory of 1.1 (a VM with an ISO
image migrated to it from a node already on 1.1) has, at its own update, the same item in both layouts; the move then
refuses, the update rolls back by itself, and the log names both paths.

## 10. Secrets, the way Proxmox VE keeps them (Antho, 2026-10-21)

Proxmox keeps no secret in environment variables: they are files in `/etc/pve/priv/`, inside the replicated cluster
file system, readable by root only; the web proxy (`pveproxy`, `www-data`) verifies session tickets with the public
half of a key pair (`authkey.pub`) while only root holds the private half (`priv/authkey.key`), rotated every 24 hours;
logins and the use of stored credentials happen in `pvedaemon`, root. Hyperlite does the same:

- **No `.env`.** Cluster secrets live in hyperlite-cfs under `/priv/` (refused to every client but root, already in its
  design, section 6), node secrets in `/etc/hyperlite/priv/` (root, 0600). The two keys of `.env` move there at the
  update: `HYPERLITE_SECRET_KEY` is retired by the change below, `HYPERLITE_ENCRYPTION_KEY` becomes
  `/priv/encryption.key`.
- **Session tickets signed by the daemon** with an Ed25519 key pair in `/priv/authkey.key`, rotated every 24 hours (the
  previous public key kept one more day, so no session ends at a rotation); the proxy holds `/authkey.pub` only and
  verifies. A compromised proxy cannot forge a session. Sign-in, token issuance and two-factor checks are daemon
  operations.
- **Stored credentials stay in the daemon.** LDAP bind, SMTP, OIDC client secret, TOTP secrets, fencing credentials are
  decrypted and used by daemon operations (`auth.ldap_bind`, `notify.send_mail`, `auth.oidc_exchange`, `auth.totp_check`,
  `fence.*`); the proxy never reads them.
- **Encrypted at rest**, beyond Proxmox: the values under `/priv/` stay encrypted with the encryption key, so a copy of
  the database (an update backup, a disk image) does not expose them.
- **The TLS key** of the web interface stays readable by the proxy's group, as `pve-ssl.key` is by `www-data`: the proxy
  terminates HTTPS.

These move with the domains of step 3 (authentication first); step 1 only moves the files.

## 11. Decisions (Antho, 2026-10-21, continued)

5. **The layout as Proxmox VE's**, table of section 9.
6. **The API runs as `www-data`**, as `pveproxy` does.
7. **The cluster configuration as files in `/etc/hyperlite`**, as pmxcfs mounts `/etc/pve`: hyperlite-cfs gets a FUSE
   mount, after the split (#525).

## 12. Step 3, first domain: authentication (2026-10-21)

Where it starts: every session token is a JWT signed HS256 with `HYPERLITE_SECRET_KEY`, a key the API reads from
`/etc/hyperlite/priv/env` and that every member of a cluster shares (the API of one member calls another's with a token
it signs itself: `rolling_update.peer_api`, `cluster_view.forward_to`). Whoever controls the API can sign any session.

Target, as Proxmox VE (`authkey.key` held by root, `pveproxy` verifies with `authkey.pub`):

- **Key pairs held by hyperlite-daemon**: Ed25519, the private key in `/etc/hyperlite/priv/authkey.key` (root, 0600),
  rotated every 24 hours. The public keys in `/etc/hyperlite/authkeys.json` (0644), the newest first, kept 8 days: a
  session of "Stay signed in" (7 days) stays valid across rotations without being renewed.
- **The API verifies only** (EdDSA, the key named by the token's `kid`); it never reads the private key.
- **The daemon signs**, and in the end only after checking the credentials itself (Proxmox: the ticket is made by the
  process that checked the password).

In three pull requests, each released and checked on the test VM:

| PR | Content | What a compromised API can still do |
|---|---|---|
| A | `app/core/tickets.py` (keys, rotation, signing, verification), the daemon makes and rotates the keys and signs (`auth.sign`); the API asks it for every session token and verifies EdDSA; HS256 still accepted, for the calls between members | ask the daemon to sign any session; sign HS256 with the shared key |
| B | sign-in, second factor, SSO exchange and password change become daemon operations (`auth.password`, `auth.totp`, `auth.webauthn`, `auth.sso`, `auth.change_password`, app/core/sign_in.py) that check before they sign; `auth.sign` removed | sign HS256 with the shared key |
| C | the calls between members stop carrying a session signed with the shared key (each member verifies the caller's own published public keys, as it already pins its TLS certificate, `/cluster/tls/<node>` → `/cluster/authkey/<node>`); `HYPERLITE_SECRET_KEY` retired once every member runs it | nothing: no key to sign with |

Development and tests run without a daemon: `HYPERLITE_AUTH_SIGNING=local` makes the API sign in-process with keys under
`HYPERLITE_ETC_DIR` (the test suite, the e2e backend); a node never sets it.

### 12.1 PR C in detail: calls between members (2026-10-21)

**Where it starts.** A member calls another member's API in a user's name in three places:
- the cluster views (`cluster_view.gather`, `forward`, `forward_to`: tasks, audit, backups, VMs of a member without a
  status, a request about a guest another member holds);
- the rolling update (`rolling_update.peer_api`), from the admin's request and then for the length of the run in a
  background thread;
- the plan shown before the run.

Each call carries an HS256 token that `security.create_peer_token` signs with `HYPERLITE_SECRET_KEY`. Every member
holds that key and accepts the token as a session of the user it names, for 4 hours. Whoever reads the key can act as
any account on every member.

**How Proxmox VE does it.** Its ticket is valid on every node because `authkey.key` is shared through pmxcfs (under
`priv/`, root only). pveproxy forwards a request to the node it names (`proxyto`) with the user's own ticket: no node
signs a ticket for a user it has not checked. Hyperlite keeps one key pair per node (no private key travels). The same
property comes from two rules: a member signs a call only for a user it has just checked, and the receiving member
checks the signature with the keys of the member that signed it.

**Target.**

1. **Each member publishes its public keys**, the content of `/etc/hyperlite/authkeys.json` (newest first, 8 days), in
   the replicated configuration under `/cluster/authkey/<node>`. This mirrors `/cluster/tls/<node>`. It is published:
   - by hyperlite-daemon, the only process that makes the keys;
   - at its start and every 10 minutes;
   - before it signs with a key made since, so a member never receives a call signed with a key it cannot find yet.

   Removing a node from the cluster removes its entry.
2. **A peer ticket**: an EdDSA JWT signed by the calling member's daemon with its current key. Its claims:

   | Claim | Value |
   |---|---|
   | `typ` | `"peer"` |
   | `iss` | the calling member |
   | `aud` | the member called (one ticket per member) |
   | `sub`, `uid` | the user |
   | `iat`, `exp` | 5 minutes |
   | `jti` | unique |

   The receiving member accepts it as a session of that user only when all of these hold:
   - its `kid` is among the keys `iss` published;
   - `iss` is a current member;
   - `aud` is itself;
   - its lifetime is at most 5 minutes (60 s of clock difference allowed).

   Then the usual checks apply (account, `uid`, a password changed since `iat`, revocation, rights). A peer ticket is
   never a session on the member that signed it.
3. **The daemon signs a peer ticket only for a credential it checks**, never for a name it is handed:
   - `auth.peer {credential, targets}` takes the bearer credential of the request in progress: a session, an API
     token, or a task ticket below;
   - it checks that credential as the API checks a request: signature, expiry, revocation, account, `uid`, password
     changed since, password to change;
   - it then signs one peer ticket per target member for that same account;
   - it refuses a peer ticket as a credential: no chain from member to member.
4. **A task ticket for the rolling update**: the run outlives the admin's request and may outlive their session.
   - When the run starts, the API asks `auth.delegate {credential}` with the admin's credential. It receives a task
     ticket: `typ: "task"`, the same account, 12 hours, this node's key.
   - The run's thread presents that ticket to `auth.peer`.
   - A task ticket is never accepted as a session by any API.
5. **The API holds no signing key for these calls.** It remembers the credential of the request it is serving
   (`peer_auth.CREDENTIAL`, set where the session is checked). It passes the credential to the threads that call
   members. It asks its daemon for the tickets. Outside a node (tests, e2e) `HYPERLITE_AUTH_SIGNING=local` runs the same
   functions in-process, as for sessions.

**Mixed versions during a rolling update.** A member that has published `/cluster/authkey/<node>` runs PR C.
- **Calling:** a member gets a peer ticket for a member that published its keys. It keeps the HS256 token for one that
  has not: that member could not verify anything else.
- **Receiving:** a member accepts an HS256 token while a current member of the cluster has not published its keys,
  since that older member still calls with it. Once every member has published, HS256 is refused for calls between
  members.
- **Sessions opened before the update:** the HS256 sessions a 1.1.0 node signed, up to 7 days for "Stay signed in"
  (PR A promised they stay valid). They are accepted when issued before this node first ran PR C, and for 7 days at
  most after it. That moment is recorded once, in `/var/lib/hyperlite/auth-hs256-cutoff`.
- **Outside a cluster:** no member is older, so only those sessions remain, and for 7 days at most.
- **The key itself:** `HYPERLITE_SECRET_KEY` stays in `/etc/hyperlite/priv/env` for that transition only. Nothing signs
  with it once every member runs PR C, except an older member's calls. The release after 1.1.1 removes it from the
  file and from the configuration copy (`config_copy.KEY_NAMES`).

**What a compromised API can still do after PR C.** It cannot sign: no key it can use, and a ticket names its member
and its audience. It can act on other members only in the name of a user whose credential it holds at that moment:
- a request in progress;
- a rolling update that an admin started.

This is the limit Proxmox VE has too: pveproxy sees the tickets it forwards. While the API runs as root (until step 4),
it can still read `authkey.key`. PR C removes the shared key that any member's API could use against all the others.
Step 4 removes the rest.

**Tests and lab.**
- Unit tests:
  - publishing and finding the keys;
  - a ticket refused for another audience, another member, an unpublished key, too long a life, `typ` other than
    `peer`;
  - a ticket refused on the member that signed it;
  - a task ticket refused as a session;
  - `auth.peer` refused without a credential or with a peer ticket;
  - the HS256 rules for mixed versions and for sessions from before the update.
- Lab, on a two-node cluster of test VMs:
  - one node on the previous build and one on this one: the cluster views and a forwarded request both ways;
  - both on this build: the same calls, HS256 refused, `/cluster/authkey/*` present;
  - a rolling update between the two.

## 13. Step 3, the domains (2026-10-22)

Authentication is done (section 12). The other domains follow the order of section 5, each in its own pull request,
claimed on #511 with `.github/scripts/claim.py`:

| Domain | Status |
|---|---|
| Host system, part 1: package updates, DNS, host name, time, syslog, power (`app/daemon/ops/host.py`) | done |
| Host system, part 2: Hyperlite's own update (`app/daemon/ops/hyperlite.py`: the job in a unit of its own, the package lists, the channel) | done |
| Host system, part 3: the HTTPS certificate (`app/daemon/ops/tls.py`: import, previous, self-signed, certbot, the API's restart) | done |
| Network, part 1: the network firewall's iptables and the VLANs of Linux bridges (`app/daemon/ops/network.py`: asked for by a network's or a VM's name, the daemon reading the bridge and the ports itself) | done |
| Network, part 2a: libvirt's networks from the network routes: create, start, stop, autostart, edit, rename, delete, the isolated network (`app/daemon/ops/network.py`; the daemon writes a new network's definition itself from checked fields, `app/core/network_create.py`, never XML from the API) | done |
| Network, part 2b: the DHCP reservations of the network routes (`network.reservation_add`, `network.reservation_delete`). The reservations VM creation, clones, containers and migration make (`app/core/network_alloc.py`) move with the VM and container domains, which own those calls (migration across two nodes included) | done |
| Storage, part 1: the ZFS pools and volumes of the storage routes (`app/daemon/ops/storage.py`: names checked again, a volume a VM uses never deleted) | done |
| Storage, part 2a: the libvirt pools of the storage routes on this node: create (directory, NFS, SMB, iSCSI, LVM), rename, delete, with the SMB credentials file, the iSCSI CHAP secret and the LVM thin pool (`storage.pool_create`, `storage.pool_rename`, `storage.pool_delete`; the daemon writes a pool's definition itself from checked fields, `app/core/storage_pools.py`) | done |
| Storage, part 2b: the volumes of the storage routes' libvirt pools (`storage.volume_create`, `storage.volume_delete`; the daemon writes a volume's definition from its checked name and size and never deletes one a VM uses). A VM's own disks (resize, move, snapshots) move with the VM domain | done |
| Storage, part 2c: what the storage routes read that needs root or a connection that may write: the LVM report of a group (`storage.lvm_volumes`) and a pool's refresh (`storage.pool_refresh`; `virStoragePoolRefresh` is refused on libvirt's read-only connection). The ZFS listings need neither (`/dev/zfs` is open to every user) | done |
| Disks, part 1: the libraries (below) | done |
| Disks, part 2: a VM's disk (below): `disk.resize`, `disk.move` (one call that waits, its progress written into the task) and `disk.cancel` (the copy is stopped in the daemon, which keeps the copies running by task); the replication checkpoints dropped by the daemon (`checkpoints.drop`), the chain forgotten by the API (`checkpoints.forget`) | done |
| VMs, part 1: power (below): `vm.power` (start, shutdown and reboot through the guest agent or ACPI, stop, reset), `vm.autostart`, `vm.pre_stop` (the hook script run as root), `vm.agent_ipv4` (an agent's question needs a connection that may write) | done |
| VMs, part 2a: processors, memory, limits, CPU pinning, advanced hardware (`vm.set_resources`, `vm.set_limits`, `vm.set_cpu_pinning`, `vm.set_disk_options`, `vm.set_boot_order`, `vm.set_balloon`, `vm.set_machine`) | done |
| VMs, part 2b: a VM's devices (`app/core/vm_devices.py`): disks (`vm.attach_disk`, the image check included; `vm.detach_disk`), interfaces with their VLANs (`vm.set_interface`, `vm.attach_interface`, `vm.detach_interface`; the daemon sets the ports itself, so `network.vlan_apply_vm` is gone), host devices (`vm.attach_hostdev`, `vm.detach_hostdev`), CD-ROM drives (`vm.set_cdrom`, `vm.eject_cdrom`, an ISO of the daemon's own library) | done |
| VMs, part 2c: a VM's firewall (`vm.set_firewall`, the nwfilter built from checked rules, `app/core/vm_firewall.py`), hook script (`vm.set_hookscript`, checked again in the root library), name (`vm.rename`, with the files named after it, `app/core/vm_rename.py`) and cloud-init drive (`vm.cloudinit_write`: written private to root, the API keeping the state) | done |
| VMs, part 3a: creation (`vm.create`, `app/core/vm_create.py`): the specification of POST /vms checked again by the daemon (its unknown fields refused at any depth), the disks, the cloud-init drive, the DHCP reservation and the definition made by it; `check_only` runs the checks alone (`?wait=false`, an ISO being copied from another node). The API keeps the lock, the task and what it records | done |
| VMs, part 3b: deletion (`vm.delete`, `app/core/vm_delete.py`), from DELETE /vms/{name} and the automatic clean-up: the daemon checks the VM is stopped again, and deletes its own disks only, as Proxmox VE does, never vSphere's way of taking a disk shared with another VM along: a disk another VM uses or reads through (a linked clone's base), or a file outside every storage pool, is kept and reported (`kept_disks`). The API forgets the VM's records | done |
| VMs, part 3c: clones (`vm.clone`, `app/core/vm_clone.py`) and templates (`template.make`, `template.deploy`, `template.rename`, `template.delete`, `app/core/vm_templates.py`): the copies, the new definitions and the template files are the daemon's, which checks the source's state, the names, the room and a template's linked clones again; the Kubernetes clusters' VMs started and stopped through `vm.power` | done |
| VMs, part 4: snapshots (`vm.snapshot_create`, `vm.snapshot_restore`, `vm.snapshot_delete`, `vm.lvm_snapshots`, `app/core/vm_snapshots.py`): libvirt's, ZFS's and LVM-thin's, checked again by the daemon (a rollback only under a stopped VM, a name ZFS would read as a list or a range refused); every check runs first with `check_only`, so the route answers a refusal before it starts the task | done |
| Containers, part 1: start, stop, settings, rename and deletion (`container.*`, `app/core/container_ops.py`), the filesystem found in the definition and accepted in Hyperlite's container directories only (below) | done |
| Containers, part 2: creation and clone (`container.create`, `container.clone`, `app/core/container_create.py`): the specification of POST /containers checked again (its unknown fields refused), the directory chosen from the pool's own definition, the image pulled and the filesystem prepared by the daemon; the API keeps the image's required variables (asked of the registry) and the records | done |
| Containers, part 3: snapshots and the console log (`container.snapshot_*`, `container.log`): read and written beside the filesystem the definition names, every check first with `check_only` | done |
| Cluster, part 1: the keys (`cluster.pubkey`, `cluster.test_node`, `cluster.trust`, `cluster.forget`, `cluster.rename_trust`): a node tested, registered, renamed and removed, its host key and its reverse trust, the node poller's checks; HA's own checks (`ha_watch.py`) stay with HA's owner | done |
| Cluster, parts 2 to 4; consoles; then backups | next: cluster, part 2 |

**How a domain moves.** Every domain follows the same rules, so the daemon stays small and a route answers as it did:

- **The implementation stays in its `app/core` module**, which hyperlite-daemon imports. The operation
  (`app/daemon/ops/<domain>.py`) checks its arguments with a model (extra fields refused, lengths bounded) and calls it.
  No argument is named `user`, `timeout`, `refused` or `failed`: the client keeps those for itself
  (`tests/test_privileged.py` checks it).
- **An operation takes the names of objects** (a network, a VM, a pool, a volume), never an interface, a path, a
  command line or XML: the daemon reads the bridge, the ports or the device itself, and writes a definition from
  checked fields.
- **The daemon checks again what must never happen**, whatever the API checked: names against the module's own
  validators, a system network, a network or a volume in use.
- **Its libvirt is this node's own**: `libvirt_utils.local_conn()`, never another member's nor a URI from the API. The
  tests fake it in one place, the `daemon_libvirt` fixture.
- **Each ops module turns its module's exceptions into the daemon's kinds** with one decorator: what the module refuses,
  `refused` with its message for the user; what does not exist, `not_found`; what is in use, `in_use`; a failure,
  `failed` with its cause. The kinds are listed in `app/daemon/__init__.py` (`registry.KINDS`); they are the daemon's
  own, not the codes of API v1 that section 4 first planned.
- **The route keeps** the permission check, the reads that need no root, the database writes, the audit log and the
  task. It calls `privileged.ask(op, user, action, target, refused=<status>, **args)`, which writes a failure into the
  audit log and answers: a refusal, the status the route gave before its domain moved (422 unless it said otherwise);
  `not_found`, 404; `in_use`, 409; without the daemon, 503; a failure, 500 `internal` (never 502, which docs/api.md
  keeps for another member of the cluster). A call outside a route (start-up, a task's thread) uses `privileged.run`
  and handles `DaemonError` itself; one that escapes a route gets the same answer from the handler in `app/main.py`.
- **The answers do not change**: same statuses, same sentences, checked by the route's existing tests. A long operation
  (an apt run) is one call that waits, its output written into the task by the daemon.

**Another node's work stays with the cluster domain.** Some storage routes act on another node from this one: a
`?node=` naming a node registered over SSH only, and the shared pools created, renamed or removed on every node
(`app/core/shared_pools.py`), through that node's libvirt over SSH. The daemon's operations act on this node only, as
pvedaemon's do. The work itself is one function of `app/core` taking the libvirt connection, so this node's goes
through the daemon and another node's keeps its SSH connection from the API, with the same checks and the same
answers. The cluster domain moves the second: a member is asked through its own API (`app/core/cluster_view.py`,
which already forwards `?node=`), whose daemon does the work; a node registered over SSH only is reached by the
daemon, which then holds the SSH keys.

**The disks domain (proposed, 2026-10-24).** `qemu-img` and the image files are used by 16 modules: the libraries
(ISOs, disks to import), a VM's disk growth and move, VM creation, clones, templates and linked clones, backups,
restores, replication, exports and OVA. Once the API runs as `hyperlite` (step 4), it can neither write nor read the
pools' files (owned by root or by QEMU, mode 0600). It moves in four parts, each in its own pull request:

| Part | What moves | Operations |
|---|---|---|
| Disks, part 1: the libraries | The ISOs and the disks to import (`app/routers/isos.py`, `app/routers/vm_disks.py`): an upload stored, an ISO renamed, a file deleted, an OVA's disks extracted (`app/core/ova.py`), the pool holding an ISO refreshed | `disk.library_store`, `disk.library_rename`, `disk.library_delete`, `disk.ova_extract` |
| Disks, part 2: a VM's disk | Growing a disk (`app/core/disk_resize.py`) and moving it to another pool (`app/core/disk_move.py`): asked by the VM's name and the disk's target, never a path | `disk.resize`, `disk.move` (one call that waits, its progress written into the task) |
| With the VM domain | The disks VM creation, clones, templates and linked clones make (`app/core/vm_builder.py`, `app/core/linked_disks.py`), and the check of an image Hyperlite did not write before a VM uses it (`app/core/disk_images.py`, `qemu-img info`): they go with the domain that uses them | moved with `vm.*` |
| A domain of its own: backups | Backups, restores, replication and exports, an OVA export included (`app/core/backups.py`, `replication.py`, `vm_export.py`, `checkpoints.py`, `backup_integrity.py`): added after the VMs to section 5's list, with the live restore of #513 | `backup.*` |

**An upload goes through a spool, as on Proxmox VE.** pveproxy writes an upload into a temporary file it owns, and
pvedaemon copies it into the storage. Hyperlite does the same. The API streams the upload into
`/var/lib/hyperlite/spool`, a directory it owns, on the libraries' own file system, so storing one is a rename, not a
second copy of gigabytes. `disk.library_store` takes that file's name in the spool, never a path. It checks the name
again, refuses an ISO that would replace another, moves the file into the library, gives it the owner and mode QEMU
needs, and refreshes the pool that lists it. The daemon moves a spool entry into a root-only staging directory before it
checks it (a regular file with one link), so the API cannot have a link stored, nor swap the entry between the check and
the move. The spool is emptied of anything older than a day at start-up. Until step 4, the API still runs as root and
the spool is only a directory: nothing changes for the user.

**What the API keeps.** It keeps listing the libraries (their directories made readable to the API's group at step 4)
and the checks that need no file (the name, an upload's size, a VM that has the ISO in its drive). It also keeps the
audit log, the tasks and `?node=`. Sharing an ISO with another node (`app/core/iso_share.py`, over SSH) moves with the
cluster domain.

**The VM domain (proposed, 2026-10-24).** Every change to a VM goes through libvirt with a connection that may write,
and the API will keep a read-only one: more than a hundred call sites (`app/routers/vms/`, `app/core/vm_builder.py`,
`app/core/vm_hardware_opts.py`, `app/core/cloudinit_edit.py`, `app/core/guest_agent.py`), plus the callers outside the
routes (the scheduled jobs, the inactive VMs' cleanup, the start at boot, maintenance). As for the networks and the
pools, an operation names the VM and gives checked fields; the daemon reads the definition itself and writes the change,
never XML from the API. It moves in four parts, each in its own pull request:

| Part | What moves | Operations |
|---|---|---|
| VMs, part 1: power | Start, shut down, stop, reboot, reset, suspend, resume, libvirt's autostart flag (`lifecycle.py`, the cleanup, the start at boot, the jobs), and the guest agent's commands (`app/core/guest_agent.py`: an agent command needs a connection that may write) | `vm.power`, `vm.autostart`, `vm.agent` |
| VMs, part 2: a VM's definition | Processors, memory, options, notes and tags, advanced hardware, the devices (disks, CD-ROM, interfaces, their VLANs), the firewall filter, the hook script, the name (`settings.py`, `hardware_opts.py`, `devices.py`, `runtime.py`, `firewall.py`, `hookscript.py`, `rename.py`) | `vm.set_*`, `vm.attach`, `vm.detach`, `vm.rename`, by field |
| VMs, part 3: creation and deletion | A VM's definition written from its checked specification (`vm_builder`), its disks (the image check of the disks domain included), its cloud-init medium, its DHCP reservations (`app/core/network_alloc.py`), clones, templates and linked clones, the deletion with its disks | `vm.create`, `vm.clone`, `vm.delete`, `vm.template` |
| VMs, part 4: snapshots | Create, revert and delete a snapshot (`snapshots.py`) | `vm.snapshot_*` |

What stays elsewhere: a migration crosses two nodes and moves with the cluster domain; the consoles and the tunnel with
the consoles; HA's own calls (`app/core/ha*.py`) are moved with HA's owner through #298, onto the operations of part 1
and part 3. Another node's VM (`?node=` naming a node registered over SSH) keeps its connection from the API until the
cluster domain, as for the pools.

**The containers domain (proposed, 2026-10-24).** A container is a libvirt LXC domain (`lxc:///system`) and a
directory: its filesystem, its snapshots beside it (`.snapshots/<name>`, rsync copies), its archives. Root deletes,
renames and writes into those directories, so **the daemon finds a container's filesystem in its definition** (the
filesystem mounted at `/`), never in the API's records (`container_storage` names the pool a container was put in):
it must be the directory named after the container in a place Hyperlite puts containers, `/var/lib/libvirt/containers`
or the `hyperlite-containers` directory of a local directory pool (`app/core/container_ops.py`). A definition naming
anything else has its filesystem left alone: a deletion keeps it and says so, a rename or a DNS change is refused. The
daemon opens the LXC driver itself (`libvirt_utils.local_lxc_conn`); the API keeps its own connection for the reads,
the container's lock (`container_snapshots.claim`) and its records (`container_meta`). Three parts:

| Part | What moves | Operations |
|---|---|---|
| Containers, part 1 | Start, stop (SIGTERM to an application image's process, the init's shutdown for a system container, the stop by force after the grace period, which names the run it stops), the settings (processors, memory, start at boot, the DNS servers written into the container's `/etc/resolv.conf`), the rename, the deletion | `container.start`, `container.stop`, `container.update`, `container.rename`, `container.delete` |
| Containers, part 2 | The creation (system and application containers: the image pulled with skopeo and umoci, the filesystem configured, the definition written from checked fields) and the clone | `container.create`, `container.clone` |
| Containers, part 3 | The snapshots and the console log read from the container's filesystem | `container.snapshot_*`, `container.log` |

A container's archives move with the backups domain, its terminal and root shell with the consoles.

**The cluster domain (proposed, 2026-10-24).** Hyperlite reaches the other nodes in two ways: a member of the cluster
through its own API (`app/core/cluster_view.py` forwards `?node=`, `rolling_update.peer_api`, the member's certificate
pinned), and every node over SSH as root with the cluster's key (`paths.SSH_DIR / "hyperlite_cluster"`, under
`/etc/hyperlite/priv`): libvirt over `qemu+ssh` for a node registered over SSH only and for a migration, `scp` for the
ISO and file copies, commands for the stats, the boot id, the LVM, SMB, VLAN and hook-script work of another node, and
the peer calls' fallback when HTTPS cannot be used. About 75 call sites in 27 files. A process that holds that key is
root on every node: once the API runs as `www-data` it must not read it. As on Proxmox VE, where the nodes trust each
other's root over SSH and pveproxy only forwards a request to the node that owns the guest, **only hyperlite-daemon
holds the cluster's keys**, and the API asks it for named work on a named node, never for a command or a libvirt URI:

- **The node is named, its address read by the daemon** from the node registry (the `nodes` table), the host key pinned
  in root's `known_hosts` when the node was registered. The API can name a registered node only; registering one stays
  an administrator's action, as today.
- **A member is still asked through its own API**, whose daemon does the work on its own host. SSH stays for what has
  no API: a node registered over SSH only, and the transfers between two hosts' roots (a migration, a disk or an ISO
  copy).
- **Each root action on another node is an operation of its domain taking `node`**: `vm.power(vm, action,
  node=...)`, `storage.lvm_volumes(group, node=...)`, the daemon opening that node's libvirt itself
  (`libvirt_utils.node_conn`) or running its fixed command there. The `?node=` branches the earlier domains left in
  the API (marked in their routes) move here.

| Part | What moves | Operations |
|---|---|---|
| Cluster, part 1: the keys | The cluster's key pair, root's `known_hosts`, the reverse trust a node is given into this one (`authorized_keys`), and their changes when a node is registered, renamed or removed (`app/core/cluster.py`) | `cluster.pubkey`, `cluster.test_node`, `cluster.trust`, `cluster.forget`, `cluster.rename_trust` |
| Cluster, part 2: a node registered over SSH only | Its VMs' power, definition and deletion, its pools and volumes, its inventory and its host's details, through the daemon's own connection to it | the domains' operations with `node`, `node.inventory`, `node.capabilities`, `node.stats` |
| Cluster, part 3: migration | The pre-checks, the disks created and the files copied on the target, `migrateToURI3`, its progress, the cleanup on the source (`app/routers/vms/migration.py`) | `vm.migrate`, `vm.migrate_cancel` |
| Cluster, part 4: the copies and the commands | An ISO copied to a node (`iso_share`), the configuration copied (`config_copy`), a member's boot id (`vm_boot`), the peer calls' SSH fallback (`rolling_update`) | `iso.copy`, `config.copy`, `node.boot_id`, `node.api` |

HA's own calls (`app/core/ha*.py`, its fencing over SSH included) move with HA's owner through #298, onto these
operations. The consoles' tunnels to another node (`spice.py`, `console.py`) move with the consoles.

**An operation that outlives the daemon.** Hyperlite's own update installs a package whose maintainer script restarts
hyperlite-daemon, then the API. `hyperlite.start_update` checks the request and starts the job in a transient unit of
its own (`systemd-run`, `app/daemon/update_job.py`), as the update watchdog runs: the job keeps going through both
restarts and writes the task itself. Checked on the test VM: an update and a return, the daemon restarted during the
update, the task succeeded and the watchdog validated.

`HYPERLITE_DAEMON_INPROCESS=1` runs the operations in the API's process through the same registry (tests, e2e, the
scale lab). `tests/test_privileged.py` checks that every operation a route names exists in the daemon.

When the last domain has moved, step 4 runs the API as `www-data`. Measured on 2026-10-22: about 325 privileged calls in
82 files remain, mostly libvirt, `qemu-img` and SSH to the other nodes.

