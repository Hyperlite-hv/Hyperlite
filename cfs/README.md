# hyperlite-cfs

Hyperlite's replicated cluster configuration, the counterpart of Proxmox VE's `pmxcfs`. The design is
[`docs/design/hyperlite-cfs.md`](../docs/design/hyperlite-cfs.md) and the architecture around it is section 15.2 of
[`docs/design/control-plane-v2-migration.md`](../docs/design/control-plane-v2-migration.md).

**State: phase B done (B1 to B4).** The daemon keeps the configuration tree in SQLite and serves it on a Unix socket, with
versions, compare-and-set, locks, guest id allocation and a checksum of the state. In cluster mode (`--cluster`) it
joins Corosync: every change is applied by every node in the order Corosync agreed, refused without quorum, and
refused while the members do not hold the same state; a member that differs (a node that was away) receives the state
of the most advanced member (latest term, then highest version) before changes resume. A change is answered once every
member confirmed it, so no answered change is lost while a quorum remains. Hyperlite can copy its writes into the daemon (shadow mode, off by default); the package builds and installs the daemon, disabled (see below).

## Build and test

Debian or Ubuntu packages: `meson ninja-build pkg-config gcc libsqlite3-dev libssl-dev libcpg-dev libquorum-dev libvotequorum-dev libcmap-dev`,
plus `clang libclang-rt-dev` for fuzzing, `corosync iproute2` for the cluster tests and `corosync-qnetd corosync-qdevice`
for the two-node one with a QDevice.

```bash
meson setup build cfs -Db_sanitize=address,undefined   # debug build with the memory and UB checkers
meson test -C build --print-errorlogs                  # C tests
HYPERLITE_CFS_BIN=build/hyperlite-cfs venv/bin/python -m pytest -q cfs/tests/python   # Python client

# Three nodes on a real Corosync, in network namespaces (root; it creates and removes the hlcfs* namespaces): the
# scenarios, the fault tests and a short soak. HYPERLITE_CFS_ROUNDS sets the soak's rounds (5; 1,000 every night, as ten parallel shards of 100),
# HYPERLITE_CFS_SEED replays a soak whose seed a failure printed.
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_BIN=build/hyperlite-cfs venv/bin/python -m pytest -q cfs/tests/cluster
# Two nodes, then two nodes with a QDevice (corosync-qnetd and corosync-qdevice installed).
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_BIN=build/hyperlite-cfs HYPERLITE_LAB_NODES=2 \
  venv/bin/python -m pytest -q cfs/tests/cluster/test_two_nodes.py
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_BIN=build/hyperlite-cfs HYPERLITE_LAB_NODES=2 HYPERLITE_LAB_QDEVICE=1 \
  venv/bin/python -m pytest -q cfs/tests/cluster/test_two_nodes.py

# Fuzzing of the request handler and of the messages between nodes (clang only).
CC=clang meson setup build-fuzz cfs -Dfuzz=true -Db_sanitize=address,undefined -Db_lundef=false
ninja -C build-fuzz && mkdir -p fuzz-work && build-fuzz/fuzz_request -max_total_time=60 fuzz-work cfs/fuzz/corpus
mkdir -p fuzz-msg && build-fuzz/fuzz_message -max_total_time=60 fuzz-msg cfs/fuzz/corpus-message
```

## Run

```bash
build/hyperlite-cfs --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket            # one node
build/hyperlite-cfs --cluster --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket  # with Corosync
```

Cluster mode needs a running Corosync with `quorum { provider: corosync_votequorum }`; every node runs one daemon.

## On an installed system

The `hyperlite` package builds the daemon on every installation and upgrade (`scripts/build-cfs.sh`, against the
distribution's own Corosync and SQLite libraries) and installs it as `/usr/local/sbin/hyperlite-cfs`. It also installs
`hyperlite-cfs.service`, **disabled**: nothing runs and nothing changes on a node until an administrator enables it.
The unit runs the daemon in local mode; `HYPERLITE_CFS_MODE=--cluster` in `/etc/default/hyperlite-cfs` makes it join
Corosync, which the package does not install. An upgrade restarts the daemon only where it was already running. If the
build fails, the installation carries on with a warning: SQLite remains Hyperlite's source of truth.

### Shadow mode (phase C)

Every configuration table (`app/repositories/cfs/tables.py`: accounts, permissions, nodes, HA, guests' settings,
storage, network, backup, replication and automation jobs, integrations) is copied into the daemon, row by row, as JSON
at `/db/<table>/<primary key>`; tables holding secrets go under `/priv/db/`, which the daemon serves to root only.
State and history (audit log, tasks, metrics, backup records, sessions) stay in each node's SQLite. SQLite triggers
record every change in a `cfs_outbox` table, in the same transaction, whatever code made it; a background thread copies
the outbox to the daemon and keeps an entry until the daemon took it, so a daemon that was down is caught up on as soon
as it answers. SQLite stays the source of truth, and a copy never fails or slows a change
(`app/repositories/cfs/shadow.py`). To turn it on: **Administration › Replicated configuration › Turn
shadow mode on**. That starts `hyperlite-cfs.service` (local mode), records the choice in the database and copies the
database once; **Turn off** stops copying and stops the service (the daemon's database is kept). The same through the
API, as an administrator: `POST /cfs/shadow/activer` and `POST /cfs/shadow/desactiver`. `HYPERLITE_CFS_SHADOW=1` in
`.env` forces it on, for a node managed by scripts.

`GET /cfs/shadow` gives the copies made, the failures and the last one, the daemon's state and, per domain, the entries
missing from the daemon (`manquants`), those it holds that SQLite no longer has (`en_trop`) and those that differ
(`differents`), with up to 20 paths of each. A failure while the daemon was down shows there until **Copy the database
again** (`POST /cfs/shadow/seed`) copies SQLite again.

`installer/test-package.sh IMAGE` checks this on a distribution, in a container: the package's dependencies resolve,
the build works and the daemon starts; the CI runs it on Debian 12, Debian 13 and Ubuntu 24.04.

When the nodes a partition misses are down for good (a two-node cluster without a QDevice, after one node died), the
partition is read-only. `build/hyperlite-cfs expected-votes 1` makes it writable again, the counterpart of Proxmox's
`pvecm expected 1`: it lists the missing nodes, explains the risk, and asks for the cluster's name before it changes
anything (`--confirm NAME` for a script).

The socket is created with mode `0600` (root only) unless `--socket-mode` says otherwise. Entries with a `priv`
component (`/priv/...`, `/nodes/<name>/priv/...`) are refused to every client that is not root, whatever the mode.

## Layout

| File | Role |
|---|---|
| `src/cfs.h` | status codes, operations and limits shared by every file and by `app/core/cfs_client.py` |
| `src/path.c` | path and name rules (every path is client input) |
| `src/proto.c` | the framed binary protocol: readers, writers, the request decoder |
| `src/store.c` | the SQLite state: tree, locks, versions, compare-and-set, list, rename, ids, checksum |
| `src/handler.c` | checks where a request arrives, reads, and the deterministic apply of a change; no I/O |
| `src/node.c` | one node of the cluster: quorum, state agreement after a membership change, changes in delivery order, answers once every member confirmed |
| `src/corosync.c` | the Corosync transport: CPG with agreed ordering, the ring number, and the quorum service of the current ring |
| `src/server.c` | the socket: one thread, `poll()`, peer credentials, answers when the cluster applied the change |
| `src/main.c` | arguments, signals, local mode (a loopback in place of Corosync) or cluster mode |
| `src/expected.c` | `hyperlite-cfs expected-votes`: the counterpart of `pvecm expected`, with its typed confirmation |
| `tests/` | C tests (`test_*.c`, `test_node.c` simulates a cluster), the Python client against the daemon (`python/`), three nodes on a real Corosync (`cluster/`: scenarios, fault tests and soak, with the checker in `harness.py`) |
| `fuzz/` | the libFuzzer targets (requests, messages between nodes) and their seed corpora |

C rules for this directory: C11, `-Wall -Wextra -Wpedantic -Wconversion -Werror`, every SQL statement bound, every
length checked before a copy, and every change must pass the sanitizer build and the fuzzer.
