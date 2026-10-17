# A clean package: nothing built or downloaded on a node

Status: **design, for review**. No code before Antho's approval (code review #448, report: "compilateur et paquets
-dev sur chaque nœud", "dépendances transitives non figées").

## Today

At every installation and every update, the package's postinst:

- **builds hyperlite-cfs** on the node (`scripts/build-cfs.sh`: meson, ninja, gcc), so the package depends on `gcc`,
  `pkg-config`, `meson`, `ninja-build` and seven `-dev` packages (`libvirt-dev`, `libsqlite3-dev`, `libssl-dev`,
  `libcpg-dev`, `libquorum-dev`, `libvotequorum-dev`, `libcmap-dev`);
- **downloads Python packages from PyPI** (`pip install --upgrade pip`, then `pip install -r requirements.txt`): 13
  direct dependencies are pinned, their own dependencies are not, nothing is checked by hash, and `libvirt-python` is
  compiled on the node. Two nodes installed a month apart do not run the same code, a node without Internet access
  cannot update from the APT repository, and a compromised PyPI package would be installed as root.

One package is tested on Debian 12, Debian 13 and Ubuntu 24.04 (CI job `package`).

## Decisions (Antho, 2026-10-18)

| Question | Decision |
| --- | --- |
| Distributions | **Debian 13 only** (the only official system of 1.0). Debian 12 and Ubuntu packages are no longer offered. |
| Python | **A ready venv in the package, plus the hashed wheels as a fallback**: if the venv does not start (Debian moved Python), it is rebuilt on the node from the wheels, without network. |
| Lock file updates | **Dependabot and CI**: Dependabot proposes, CI tests, merged when green; pip-audit reports known vulnerabilities. |

## What changes

### Built once, in CI

The `package` job builds in a `debian:13` container:

1. `hyperlite-cfs`, release build, the same as today's postinst build, installed in the package at
   `/usr/lib/hyperlite/hyperlite-cfs` (the unit's `ExecStart` follows; `/usr/local/sbin/hyperlite-cfs` stays as a
   symlink for one version, for the scripts and docs that name it).
2. The Python environment: `python3 -m venv`, then `pip install --require-hashes --no-deps -r requirements.lock`
   from wheels built in the same container (`pip wheel`). The venv goes in the package at its final path (today
   `/root/hyperlite/venv`; moved with the application when it leaves `/root`, a separate project).
3. The wheels themselves, at `/usr/lib/hyperlite/wheels/`, with `requirements.lock`.

### `requirements.lock`

Generated from `requirements.txt` by `pip-compile --generate-hashes` (pip-tools, a development dependency): every
package, direct or not, with its exact version and hashes. `requirements.txt` stays the list a human edits.
Dependabot updates the lock file; a CI check fails when the two disagree.

### The postinst

- No `pip`, no `meson`, no network. It checks that `venv/bin/python -c 'import app.main'` works.
- If it does not (the venv's Python is gone or changed): it rebuilds the venv from `/usr/lib/hyperlite/wheels`
  (`pip install --no-index --require-hashes`), then checks again; still failing, the update stops and the safety net
  of #393 puts the previous version back.
- The preflight of the Python dependencies stays (it checks the venv instead of pip's work).

### `Depends`

Removed: `gcc`, `pkg-config`, `meson`, `ninja-build`, `python3-pip`, and every `-dev` package. Added: the runtime
libraries the daemon links (read on lab node hlc1): `libcmap4`, `libcorosync-common4`, `libcpg4`, `libqb100`,
`libquorum5`, `libvotequorum8`, `libsqlite3-0`, `libssl3t64`, `libsystemd0`, `libzstd1`, `zlib1g`, `libcap2`; and
`libvirt0` for libvirt-python. `python3 (>= 3.13), python3 (<< 3.14)` and `python3-venv` stay (the fallback).
On a node updated from an older version, the build tools are left installed: removing them is the administrator's
choice (`apt autoremove` once they are no longer needed by anything).

### The ISO and the offline update

They already carry wheels (`installer/build-offline-bundle.sh`): they carry the same package instead, and the offline
bundle no longer needs its own wheels.

## Size

Today's package is 14 MB. The venv is 91 MB unpacked and 18 MB compressed with xz, the package's compression
(measured on lab node hlc1); the wheels, already compressed, add about 20 MB (estimated). The package grows to about
50 MB. An update downloads it once per node; the offline update file grows by about 35 MB.

## Tests

- CI `package` job, on `debian:13` only: install from scratch, update from the previous release, the venv broken on
  purpose (its `python` link removed) and rebuilt from the wheels with the network cut, then `apt-get purge`.
- The lab: the real package on hlc1-3 as an update, `lab416.py`, and the rollback drill (#395) back to the version
  before.
- `dpkg -L` of the package lists no file under a build directory, and `Depends` lists no `-dev` package (a test).

## Order of work

1. `requirements.lock` and its CI check, Dependabot on it.
2. The CI build of hyperlite-cfs and the venv, the package's new contents and `Depends`, the postinst; Debian 12 and
   Ubuntu removed from the `package` matrix and from the APT repository's publishing.
3. The ISO and the offline update file use the new package.

## Answered (Antho, 2026-10-18)

1. No node runs Debian 12 or Ubuntu: their packages are simply no longer published.
2. The daemon and the wheels go to `/usr/lib/hyperlite/` in this project; the application follows when it leaves
   `/root`.
