# Design: `hyperlite-cfs`, the replicated cluster configuration

Status: **accepted (2026-10-01).** Phase A (local mode) and step B1 of phase B (Corosync, quorum, agreement on one
state) are in `cfs/`; B2 to E are to come.

Context: `docs/design/control-plane-v2-migration.md`, section 15.2. The maintainers chose Proxmox VE's architecture
on Proxmox VE's foundations: Corosync for membership, quorum and ordered messages, and a replicated configuration
database of our own, the counterpart of Proxmox's `pmxcfs`, written in C (Q11).

Sources: the Proxmox VE documentation (`pmxcfs.adoc`, `pvecm.adoc` in `proxmox/pve-docs`) and the Corosync manual
pages (`cpg_overview(3)`, `cpg_mcast_joined(3)`, `cpg_model_initialize(3)`, `votequorum(5)` in `corosync/corosync`).
Quotes are from those pages. `pmxcfs` is AGPL-3.0 and Hyperlite is PolyForm Noncommercial: **its source code is not
read or copied**; only its documented behaviour is reused.

## 1. What it must do

What Proxmox documents for `pmxcfs`, which is the target:

- "a database-driven file system for storing configuration files, replicated in real time to all cluster nodes
  using corosync";
- "Although the file system stores all data inside a persistent database on disk, a copy of the data resides in
  RAM. This imposes restrictions on the maximum size, which is currently 128 MiB";
- "Read-only when a node loses quorum";
- "Includes a distributed locking mechanism";
- files under `priv/` "are only accessible by root".

For Hyperlite this means:

1. Every node holds the **whole** configuration: users, permissions, nodes, VM definitions (by numeric id),
   storage, networks, firewall, HA, schedules and secrets. Telemetry, task logs and history are not in it.
2. A write is applied on **every** node, in the **same order**, or on none of them.
3. A node that is not in the quorum partition **refuses every write**. It still serves reads from its copy.
4. A node that was away (rebooted, cut off) **catches up** before it serves anything again.
5. Ephemeral per-node status (the counterpart of what `pvestatd` sends "to all nodes") travels through the same
   channel but is **never written to disk**.
6. Single node: the same daemon in local mode, without Corosync.

## 2. What Corosync gives, and what it does not

From the Corosync manual pages:

- **CPG** (closed process group) is "used to create distributed applications that operate properly during cluster
  partitions, merges, and faults". It delivers messages to the members of a group and "configuration changes"
  (membership).
- **Ordering**: `CPG_TYPE_AGREED`: "All processors must agree on the order of delivery. If a message is sent from two
  or more processes at about the same time, the delivery will occur in the same order to all processes."
- **Not provided**: `CPG_TYPE_SAFE` ("all processes must have a copy of the message before any delivery takes
  place") "is unimplemented in the CPG library".
- **Encryption**: "If encryption is enabled in corosync.conf, the CPG library will encrypt and authenticate message
  contents."
- **Quorum**: `votequorum` counts the votes of the members against `expected_votes` and reports whether the node
  is quorate (`quorum_getquorate(3)`).

What follows for the design:

- Agreed order gives every node the same sequence of writes **inside one membership**. Applying them in that order
  yields the same database everywhere, without a consensus protocol of ours.
- Without `SAFE` delivery, when the membership changes a node may have applied a message that another node never
  received, for example because the sender crashed mid-send. **Every membership change is therefore followed by a
  state synchronisation** (section 4) before writes resume. This is the hardest part of the daemon, and the test
  plan concentrates on it.

## 3. Data model

- **A tree of small files**, like `/etc/pve`. Each entry has a path, a type (file or directory), content (bytes),
  modification time and a **version** (a 64-bit counter, the cluster-wide write number that last changed it).
- Stored in **SQLite** on each node (`/var/lib/hyperlite-cfs/config.db`), as `pmxcfs` does, and held in RAM. Limit
  128 MiB, the same as Proxmox. Writes above the limit are refused.
- **Layout** (proposal):
  - `cluster/`: `corosync.conf`, settings, auth domains (`sso`, `ldap`), datacenter-wide firewall;
  - `rbac/`: users, groups, roles, ACLs, API token hashes, security keys;
  - `nodes/<name>/`: node settings, `qemu/<vmid>.json` and `lxc/<ctid>.json` (a guest belongs to the node that
    runs it, as in `/etc/pve/nodes/<NAME>/qemu-server/<VMID>.conf`), `priv/`;
  - `storage/`, `network/`, `ha/`, `backup/`, `jobs/`, `notifications/`;
  - `priv/`: cluster secrets (root only).
- Content is **JSON**, one object per file, written by the Python repositories. A guest's libvirt XML stays generated
  from its JSON.
- **Cluster-wide version**: every applied write increments a counter stored with the data. Two nodes with the same
  counter and the same checksum hold the same tree.

## 4. Protocol

### 4.1 Writes

1. A Python process (the API, the scheduler...) sends a request on a local Unix socket: `write(path, content,
   expected_version)`, `mkdir`, `delete`, `rename`, `lock`, `unlock`.
2. The daemon refuses at once if this node is **not quorate**, or if it is still synchronising.
3. Otherwise it multicasts the request to the group with `CPG_TYPE_AGREED`, tagged with its node id and a request
   id.
4. **Every** node, the sender included, applies messages **in delivery order**, each in one SQLite transaction:
   - check `expected_version`, if given; a mismatch is a conflict and nothing changes, on every node alike;
   - apply, increment the cluster version, commit.
5. The sender answers its client only when **its own copy** of the message has been delivered and applied. A reader
   on the same node therefore sees its own write, and the result (applied or conflict) is the same on every node.

`expected_version` is compare-and-set: two administrators changing the same VM at the same time get one success and
one clear conflict, never a silent overwrite.

### 4.2 Locks

The documented "distributed locking mechanism" is implemented with ordered messages:
- `lock(name, owner, ttl)` succeeds on every node for the first delivered request and fails for the others;
- locks held by a node that leaves the membership are released at the membership change;
- a TTL guards against a holder that hangs while it stays a member.

Used for: a VM being migrated, a backup in progress, id allocation, the HA manager role.

### 4.3 Id allocation

A new guest id is the result of an ordered write to `cluster/next-ids`, so two nodes can never hand out the same id.
Proxmox's interface "will ask the backend for a free VMID" in the same spirit.

### 4.4 Membership change and state synchronisation

On every configuration change delivered by CPG:

1. Every node stops applying writes and answers clients "synchronising, retry". The window is short.
2. Every member multicasts `(node id, cluster version, checksum of the tree)`.
3. When all of them are delivered, every member computes the same choice from the same data: the **source** is the
   member with the highest version, ties broken by the lowest node id.
4. Members whose `(version, checksum)` differs from the source's receive the source's tree:
   - entries newer than their own are sent as a diff when possible;
   - a full copy is sent otherwise, for example when the checksums differ at the same version, which means
     divergence.
5. Every member confirms. Writes resume only when **all** members match **and** the partition is quorate.

Two consequences, both deliberate:
- A partition that was **not quorate** has written nothing, since writes are refused without quorum. On merge, the
  quorate side's tree wins without conflict.
- A write applied on some nodes just before a crash, and lost by the others, is either kept, if the node that
  applied it becomes the source, or reverted. The only client that could have received a success answer is the one
  on the node that applied it. The daemon therefore answers only after delivery (4.1, step 5), and a client that got
  no answer must read back before retrying. The test plan has to prove that **no acknowledged write is lost while a
  quorum remains**.

### 4.5 Ephemeral status (`hyperlite-statd`)

- Each node multicasts its status: guest states, CPU and memory figures, storage use. It does this every 10 s and on
  libvirt events, as one compressed message per node.
- Every node keeps the latest status of every node **in RAM only**, and serves it on the socket.
- The API answers list pages from this table, which is how 1,000 VMs are listed without querying them (section
  15.2.3 of the control plane design).

## 5. Quorum

- `votequorum`, every node configured with one vote (`quorum_votes: 1`). Writes are allowed only while
  `quorum_getquorate()` reports quorate.
- **Two nodes**: Hyperlite does **not** enable `two_node: 1`. That option sets quorum "artificially to 1", so both
  halves of a split could write. Two-node clusters follow Proxmox's recommendation of a QDevice (`corosync-qnetd` on
  a third machine). Without one, the survivor of a failure is read-only until the administrator runs the equivalent
  of `pvecm expected 1`.
- Single node: local mode, always writable, no Corosync.

## 6. Security

- Corosync encryption and authentication on: `crypto_cipher` and `crypto_hash`, with an `authkey` generated when the
  cluster is created and copied to joining nodes over the existing SSH trust.
- Socket `/run/hyperlite-cfs/socket`, root only. The unprivileged API process (`hyperlite-proxy`) never reaches it
  directly; it goes through `hyperlite-daemon`, like `pveproxy` and `pvedaemon`.
- `priv/` and `nodes/<name>/priv/` are refused to every client except root's, as in `/etc/pve/priv/`. Values in them
  stay encrypted with Hyperlite's key, as they are in SQLite today.
- Every input that ends up in a path is validated: no `..`, no NUL, bounded length, a fixed character set.
- C memory safety: built with `-D_FORTIFY_SOURCE=3 -fstack-protector-strong`, run under AddressSanitizer and
  UndefinedBehaviorSanitizer in CI, and fuzzed on the socket and message decoders.

## 7. Access from Python

- `app/repositories/cfs/`: a repository implementation per domain over the socket, next to `sqlite/`. The
  repositories written in phase 1 are the only callers, so the routers do not change.
- A small client (`app/core/cfs_client.py`) with a length-prefixed binary protocol, timeouts, and the exceptions
  `ReadOnly` (no quorum), `Conflict` (version), `Synchronising` (retry) and `TooLarge`.
- A **FUSE view** of the tree, mounted read-only at `/etc/hyperlite/cluster` for administrators and scripts, is
  optional and comes last.

## 8. Phases

| Phase | Content | Exit criterion |
|---|---|---|
| A | Daemon in **local mode**: SQLite tree, socket, versions, compare-and-set, locks, ids. No Corosync | unit tests, ASan and UBSan clean, fuzzing of the decoders |
| B | **Cluster mode** on a 3-node lab: CPG, agreed apply, state synchronisation, votequorum read-only | the fault tests of section 9 pass 1,000 runs in a row |
| C | **Shadow mode** in Hyperlite: the repositories write to SQLite **and** `hyperlite-cfs`, a divergence report | no divergence over two weeks on the dev cluster |
| D | **Source of truth**: reads from `hyperlite-cfs`, SQLite kept read-only one release for rollback | a rollback drill done once |
| E | `hyperlite-statd` over the same channel, list pages from the status table | 1,000 VMs listed in under 200 ms |

### 8.1 Phase B, step by step

| Step | Content | State |
|---|---|---|
| B1 | Corosync transport (CPG agreed order, quorum service); changes applied by every member in delivery order; refused without quorum; after each membership change, every member sends its state and changes resume only when all states are equal and quorate; a three-node test on a real Corosync in network namespaces | done |
| B2 | State transfer: a member that differs receives the source's tree (section 4.4), so a node that was away catches up instead of keeping the cluster read-only | to do |
| B3 | Fault tests: kill a node mid-write (sender, receiver, source of a transfer), a node back after a thousand writes, the checker of section 9, run 1,000 times | to do |
| B4 | `pvecm expected 1`'s counterpart for two nodes, with its typed confirmation; QDevice in the test lab | to do |

### 8.2 Decisions taken while building B1

Found by reading phase A against what cluster mode needs:

- **A change must give the same result on every node.** Phase A stamped `mtime` and judged lock expiry with the local
  clock at apply time; across nodes that differs. A change now carries its sender's time stamp and every node applies
  it with that time. Clocks out of step only shift `mtime` and lock expiry by the skew, the same on every node (as
  Proxmox, the cluster relies on NTP).
- **Locks are part of the replicated state.** They were a table in RAM, so a node that restarted or joined held other
  locks than its peers. They are now rows of the same SQLite database, covered by the checksum and copied by the state
  transfer. Each lock records the node it was taken from; when a node leaves the membership, every member releases its
  locks at the same point of the delivery order. Each lock change bumps the cluster version.
- **A node that cannot apply a change leaves.** If SQLite fails on one node (disk full, I/O error) while the others
  applied the change, that node would silently diverge. It now leaves the CPG group, refuses changes, and logs why;
  restarting it brings it back through the state agreement.
- **One code path at every size.** Local mode is a cluster of one node: the same node code with a loopback in place of
  Corosync, always quorate. What runs on one node is what runs on fifty.
- **The quorum view travels with the state.** The quorum service and CPG are separate streams, so a node of a minority
  could send a change before it learns it lost the quorum. Every member therefore drops changes from a membership
  change until all members sent a state, and a member sends its quorum view with its state (read from the quorum
  service when CPG reports the membership, after Corosync finished its synchronisation). A minority never agrees,
  so it never applies anything.
- **Messages from other nodes are untrusted input**, like the local socket: they have their own fuzz target.

## 9. Tests

- **Unit, in C**: tree operations, versions, compare-and-set, locks, id allocation, the encoders and decoders.
- **Cluster tests**, with Corosync in network namespaces on one machine, scripted:
  - kill a node mid-write, whether the sender, a receiver or the source of a synchronisation;
  - a partition 2 against 1, writes on both sides (refused on the minority), then a merge;
  - a node that comes back after a thousand writes;
  - concurrent compare-and-set on the same path from three nodes;
  - lock holder killed, lock holder hung (TTL);
  - a 128 MiB tree; 1,000 guests; 100 writes per second.
- **Checker**: every acknowledged write is present on every node once the cluster is whole again, and every node's
  checksum matches.
- `corosync-vqsim` (Debian package `corosync-vqsim`, a votequorum simulator) for the quorum edge cases.

## 10. Size and risks

- **Size**: XL. Phase A is about 3 to 5 PRs. Phase B is the largest (synchronisation and fault tests). C to E reuse
  the phase 1 repositories.
- **Risk: correctness of the synchronisation.** It is the one place where Hyperlite writes distributed-systems logic
  itself, against the brief's "no distributed storage of our own". This is accepted by the maintainers' choice, and
  the gate is the fault-test exit criterion of phase B.
- **Risk: C.** Memory errors in a root daemon that holds every secret. Mitigations are in section 6. Every PR to the
  daemon needs a second reviewer.
- **Risk: the network.** Proxmox requires "latencies under 5 milliseconds (LAN performance)". Nodes linked over a
  WAN VPN cannot form a cluster. The installer measures latency and refuses above 10 ms.

## 11. Decisions (maintainer, 2026-10-01)

- **C1. A file view, as `/etc/pve`.** The tree is mounted read-only with FUSE at `/etc/hyperlite/cluster`, for
  troubleshooting and scripts (`cat`, `ls`). Writes go only through the socket. It comes last (section 7).
- **C2. Status every 10 s, and at once on a libvirt lifecycle event** (a guest started, stopped, migrated).
- **C3. Meson.** The maintainer asked for a recommendation. Meson is the choice because:
  - it has a test runner (`meson test`);
  - the sanitizers of section 6 are one option away (`-Db_sanitize=address,undefined`);
  - it finds Corosync's and SQLite's libraries through `pkg-config`;
  - Debian's `debhelper` builds Meson projects without extra rules.
