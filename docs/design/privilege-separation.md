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
  "request_id": "..."}`, answered `{"ok": true, "result": ...}` or `{"error": code, "detail": ...}`, the codes of API v1.
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
