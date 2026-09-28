# Design: automatic HA restart with reliable fencing

Status: **proposal, waiting for the maintainer's decision.** No code is written before it is accepted.

## 1. Where we are

- `app/core/ha.py` protects a VM whose disks are all on a shared (NFS, `netfs`) pool. The node poller
  (`cluster.py::_poll_nodes`, every `POLL_INTERVAL_S` = 60 s) marks a node `hors_ligne` when the libvirt
  connection over SSH fails, and `alert_for_down_node()` writes an audit alert.
- Recovery is **manual on purpose**: an admin clicks "recover". Before redefining the VM elsewhere,
  `_attempt_ssh_fence()` tries to kill the qemu process over SSH. If SSH is also down, the fence fails and the
  recovery goes on anyway, relying on QEMU's image write lock as the last safety net.
- The HA record follows a live migration (`ha.follow_migration`, merged with the maintenance mode).
- Hyperlite runs on **one controller host** (the local node); the other nodes have no Hyperlite agent and are
  driven over SSH. If the controller itself fails, nothing watches the cluster (see the replicated
  configuration design, `docs/design/cluster-config.md`).

The reason recovery is manual is still valid: "unreachable" is not "dead". A node cut from the network but
still running its VMs would get a second copy of each VM started elsewhere, on the same NFS disk, which
corrupts the disk. Automatic restart is only safe when the old copy is **known** to be stopped.

## 2. Goal and non-goals

Goal: restart a protected VM on another node **automatically**, and only when its previous node is **fenced**:
powered off, or proven unable to write to the VM's disks. "Unreachable" alone never triggers a restart.

Non-goals, at least in this design:
- surviving the loss of the controller (covered by the replicated configuration design);
- live state preservation: this is a restart (like Proxmox HA or vSphere HA), not fault tolerance or
  lockstep;
- storage other than NFS (Ceph/iSCSI shared storage comes later, same principles).

## 3. Fencing options

| Option | How | Proves the node is off? | Hardware needed | Main risk |
|---|---|---|---|---|
| A. IPMI / Redfish | The node's BMC powers it off, then its power state is queried until "off" (`fence_ipmilan`, `fence_redfish` from the `fence-agents` package) | Yes, from an independent power path | Server with a BMC (iLO, iDRAC, XCC...) | BMC on the same failed network as the node: fencing fails, so no restart (safe, not available) |
| B. Intel AMT (vPro) | Same, through AMT's WS-MAN power control (`fence_amt_ws`) | Yes | vPro PC with AMT provisioned (MEBx), for example the HP EliteDesk 800 G6 | AMT must be provisioned and reachable; the port and TLS setup vary by firmware |
| C. Storage lease + hardware watchdog (self-fencing) | Each running VM holds a lease on the shared storage (libvirt `virtlockd` or `sanlock`). A node that loses its lease stops its VMs or is reset by its watchdog (`/dev/watchdog`, softdog as fallback). The controller waits "lease timeout + margin" before restarting | Yes, by construction: after the timeout the old node cannot hold the lease any more | Shared storage that supports locks (NFSv4) and a watchdog; no BMC needed | Needs careful timeouts; a node with a hung kernel and no hardware watchdog does not self-reset |
| D. SSH fence (today) | `kill -9` of the qemu process over SSH | Only when SSH answers; proves nothing when the node is unreachable | None | Useless in the case that matters (network or host dead) |

Option D stays as a first, cheap attempt, but it never counts as a confirmed fence by itself when the node is
unreachable.

## 4. Recommended design

**Layered fencing, opt-in per VM, and no action without confirmation.**

1. **Per-node fence method** (new table `node_fencing`): `ipmi`, `redfish`, `amt` or `lease_only`. The
   address and credentials are encrypted with the existing Fernet key (`secrets_crypto.py`) and never returned
   by the API. A **"Test fencing"** button queries the power **status** only; it never powers anything off.
2. **Leases for every protected VM** (option C, as a second barrier): libvirt's `lockd` lock manager, with its
   lockspace directory on the same NFS share as the VM disks. QEMU then refuses to open a disk whose lease is
   held by another node. That prevents a double start even if everything else went wrong. It is a per-host
   libvirt setting (`qemu.conf`, `qemu-lockd.conf`) that Hyperlite's preflight checks and reports; the
   installer can set it up.
3. **Detection** is a dedicated HA heartbeat, separate from the 60 s inventory poll: every 10 s, per node with
   protected VMs, a libvirt ping plus an SSH `true`. A node is **suspect** after 3 consecutive failures
   (about 30 s) and **failed** after 6 (about 60 s), both configurable.
4. **Controller isolation check** before any fencing: the controller must still reach a **majority** of the
   nodes plus a **witness** (a configurable address: the NAS, the gateway, or a small third node). If it
   cannot, it is probably the isolated party: it logs and notifies, and does nothing.
5. **Fence**, then **confirm**: power off through the BMC or AMT, then poll the power state until "off"
   (timeout 60 s). For `lease_only`, wait for lease timeout + margin instead. No confirmation means no restart:
   the VM goes to `manual`, and the admin is notified with the reason.
6. **Placement**: the first node, in the admin's priority order, that is online, not in maintenance, passes the
   compatibility diagnostic (`cluster_compat`) and has enough free RAM. VMs restart one by one in HA priority
   order.
7. **Per-VM state machine**, persisted in `ha_protected_vms` (new columns `etat_ha`, `derniere_action`):
   `ok → suspect → fencing → recovering → ok`, or `→ manual` on any doubt. A restart limit (for example 3 in
   one hour per VM) avoids a restart loop.
8. **Opt-in**: each protected VM gets "Automatic restart" (off by default). Without it, today's manual
   recovery stays, now with the fence status shown.
9. **Audit and notifications**: every transition is an audit entry and a notification through the existing
   channels (`ha_suspect`, `ha_fenced`, `ha_restarted`, `ha_manual_required`).

### Behaviour without quorum

Hyperlite has no cluster-wide quorum yet (see the configuration design). Until it does:
- only the controller decides, and only after the isolation check of point 4;
- **with two nodes, a witness is mandatory** to enable automatic restart, otherwise the option stays greyed
  out with the reason;
- if the controller is the node that fails, no automatic restart happens. This is stated in the UI.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Double start (split brain) on the shared disk | Confirmed fence before any restart, plus leases (QEMU refuses a held disk), plus the isolation check |
| BMC or AMT unreachable in the very failure that matters | Fencing fails, so the VM goes to manual. Unavailability, never corruption |
| Flapping network restarting VMs for nothing | Suspect/failed thresholds, witness, per-VM restart limit |
| BMC credentials leak | Encrypted at rest, admin-only, never returned by the API, audit on change |
| Wrong NFS lock behaviour | Preflight test of the lockspace (take a lease from two nodes, the second must fail) before enabling |
| Controller failure | Out of scope here, stated in the UI; see the replicated configuration design |

## 6. Steps (one pull request each)

1. `node_fencing` table, API, UI and "Test fencing" (status query only). Fence agents called with
   `subprocess`, arguments validated, no shell.
2. Lease setup check in preflight, plus documentation for `lockd` on the NFS share.
3. HA heartbeat, isolation check, witness setting, per-VM state machine; still **no automatic action**: only
   alerts that say what would have happened ("dry run" mode).
4. Automatic restart, opt-in per VM, with the restart limit.
5. UI: fence status, HA state per VM, history; notifications.
6. Tests: unit tests with a fake fence agent; e2e with two simulated nodes; a manual test on real hardware:
   pull the network cable of a node, check the fence, the single restart, and that the disk is intact.

## 7. Questions for the maintainer

1. Which fencing does each node have? Is the EliteDesk 800 G6's AMT provisioned (MEBx), and reachable from
   the controller?
2. Is there a third machine or the NAS to act as the witness?
3. What detection delay is acceptable before a restart (about 60 s by default)?
4. Do we start with step 3 (dry run) on the dev setup before enabling any automatic action?
