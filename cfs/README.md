# hyperlite-cfs

Hyperlite's replicated cluster configuration, the counterpart of Proxmox VE's `pmxcfs`. The design is
[`docs/design/hyperlite-cfs.md`](../docs/design/hyperlite-cfs.md) and the architecture around it is section 15.2 of
[`docs/design/control-plane-v2-migration.md`](../docs/design/control-plane-v2-migration.md).

**State: phase A, local mode.** The daemon keeps the configuration tree of one node in SQLite and serves it on a Unix
socket. It has versions, compare-and-set, locks, guest id allocation and a checksum of the tree. Corosync, the
replication between nodes and quorum come in phase B. Nothing in Hyperlite uses the daemon yet.

## Build and test

Debian or Ubuntu packages: `meson ninja-build pkg-config gcc libsqlite3-dev libssl-dev`, plus `clang libclang-rt-dev`
for fuzzing.

```bash
meson setup build cfs -Db_sanitize=address,undefined   # debug build with the memory and UB checkers
meson test -C build --print-errorlogs                  # C tests
HYPERLITE_CFS_BIN=build/hyperlite-cfs venv/bin/python -m pytest -q cfs/tests/python   # Python client

# One minute of fuzzing of the request handler (clang only).
CC=clang meson setup build-fuzz cfs -Dfuzz=true -Db_sanitize=address,undefined -Db_lundef=false
ninja -C build-fuzz && mkdir -p fuzz-work && build-fuzz/fuzz_request -max_total_time=60 fuzz-work cfs/fuzz/corpus
```

## Run

```bash
build/hyperlite-cfs --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket
```

The socket is created with mode `0600` (root only) unless `--socket-mode` says otherwise. Entries with a `priv`
component (`/priv/...`, `/nodes/<name>/priv/...`) are refused to every client that is not root, whatever the mode.

## Layout

| File | Role |
|---|---|
| `src/cfs.h` | status codes, operations and limits shared by every file and by `app/core/cfs_client.py` |
| `src/path.c` | path and name rules (every path is client input) |
| `src/proto.c` | the framed binary protocol: readers, writers, the request decoder |
| `src/store.c` | the SQLite tree: versions, compare-and-set, list, rename, ids, checksum |
| `src/locks.c` | named locks with an owner and a time to live |
| `src/handler.c` | one request in, one answer out; no I/O, so the tests and the fuzzer drive it directly |
| `src/server.c` | the socket: one thread, `poll()`, peer credentials |
| `src/main.c` | arguments and signals |
| `tests/` | C tests (`test_*.c`), Python tests against the daemon (`python/`) |
| `fuzz/` | the libFuzzer target and its seed corpus |

C rules for this directory: C11, `-Wall -Wextra -Wpedantic -Wconversion -Werror`, every SQL statement bound, every
length checked before a copy, and every change must pass the sanitizer build and the fuzzer.
