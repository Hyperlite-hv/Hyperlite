# hyperlite-cfs

Hyperlite's replicated cluster configuration, the counterpart of Proxmox VE's `pmxcfs`. The design is
[`docs/design/hyperlite-cfs.md`](../docs/design/hyperlite-cfs.md) and the architecture around it is section 15.2 of
[`docs/design/control-plane-v2-migration.md`](../docs/design/control-plane-v2-migration.md).

**State: phase B, step B3.** The daemon keeps the configuration tree in SQLite and serves it on a Unix socket, with
versions, compare-and-set, locks, guest id allocation and a checksum of the state. In cluster mode (`--cluster`) it
joins Corosync: every change is applied by every node in the order Corosync agreed, refused without quorum, and
refused while the members do not hold the same state; a member that differs (a node that was away) receives the state
of the most advanced member (latest term, then highest version) before changes resume. A change is answered once every
member confirmed it, so no answered change is lost while a quorum remains. Nothing in Hyperlite uses the daemon yet.

## Build and test

Debian or Ubuntu packages: `meson ninja-build pkg-config gcc libsqlite3-dev libssl-dev libcpg-dev libquorum-dev libvotequorum-dev`,
plus `clang libclang-rt-dev` for fuzzing and `corosync iproute2` for the cluster test.

```bash
meson setup build cfs -Db_sanitize=address,undefined   # debug build with the memory and UB checkers
meson test -C build --print-errorlogs                  # C tests
HYPERLITE_CFS_BIN=build/hyperlite-cfs venv/bin/python -m pytest -q cfs/tests/python   # Python client

# Three nodes on a real Corosync, in network namespaces (root; it creates and removes the hlcfs* namespaces): the
# scenarios, the fault tests and a short soak. HYPERLITE_CFS_ROUNDS sets the soak's rounds (5; 1,000 every night),
# HYPERLITE_CFS_SEED replays a soak whose seed a failure printed.
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_BIN=build/hyperlite-cfs venv/bin/python -m pytest -q cfs/tests/cluster

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
| `tests/` | C tests (`test_*.c`, `test_node.c` simulates a cluster), the Python client against the daemon (`python/`), three nodes on a real Corosync (`cluster/`: scenarios, fault tests and soak, with the checker in `harness.py`) |
| `fuzz/` | the libFuzzer targets (requests, messages between nodes) and their seed corpora |

C rules for this directory: C11, `-Wall -Wextra -Wpedantic -Wconversion -Werror`, every SQL statement bound, every
length checked before a copy, and every change must pass the sanitizer build and the fuzzer.
