# Design: automatic HA restart with reliable fencing

Status: **the maintainer's decisions of 2026-10-17 (#333) applied to the cluster; steps 2 to 4 waiting for the
maintainer's acceptance.** Step 1 (the watchdog self-fence) is merged (#444, #459). No code of steps 2 to 4 is written
before this document is accepted.

The first version of this document (one controller driving SSH hosts, a witness, a dry run first) is replaced: since
1.0 every node runs Hyperlite on the replicated configuration, a node without quorum changes nothing (#436), and one
node, the leader, runs the cluster-wide work (`app/core/cluster_lead.py`).

## 1. Goal

Restart a VM with HA turned on, on another member of the cluster, **automatically**, and only once its previous node
is **known to be stopped**: two copies of a VM on its shared disk corrupt it. "Unreachable" alone never triggers a
restart. This is a restart, as Proxmox VE's HA and vSphere HA do, not fault tolerance.

Out of scope: a cluster of SSH-driven hosts (one controller), where recovery stays manual; storage other than shared
file pools (NFS, SMB: `uses_shared_storage`); live state preservation.

## 2. Where we are

| Piece | State | Where |
|---|---|---|
| A VM protected only when every disk is on a shared pool; its definition kept in the replicated `ha_protected_vms` and refreshed by the node that runs it | done | `app/core/ha.py` (#442) |
| **Step 1**: a node that runs HA VMs arms its watchdog (hardware, else softdog), feeds it every 5 s while it has the quorum, is reset after 60 s without it; disarmed cleanly when it has no HA VM left or the service stops; publishes `armed`, `reset_within_s` | done | `app/core/ha_selffence.py` (#444, #459) |
| Fencing settings per node (IPMI, Redfish, AMT, `lease_only`) with a **status** test only | done | `app/core/ha_fencing.py` |
| Manual recovery: an administrator restarts a protected VM elsewhere, after an SSH `kill` attempt | done | `ha.recover` |
| A watcher that says what it *would* do, written for one controller (witness, SSH probes) | to replace | `app/core/ha_watch.py` |
| The leader lock; read-only without quorum; each node's status every 10 s with its age | done | `cluster_lead`, `statd` |

## 3. Decisions (maintainer, 2026-10-17)

| Question | Decision |
|---|---|
| Fencing | All three: watchdog self-fence (any PC), IPMI/Redfish, Intel AMT |
| Which VMs | Only VMs with HA turned on, one by one, as on Proxmox |
| Where | The online member with the most free memory; optionally a list of preferred nodes per VM (no HA groups) |
| Delay before restarting | About 2 minutes: the lost node has time to fence itself, and a short network cut triggers nothing |
| Self-fence | Reboots the node, as on Proxmox; a node without HA VMs never arms its watchdog |
| Default | Active by default: no dry-run step |
| Quorum | Replaces the isolation check and the witness; only the leader acts; on two nodes, the primary node's second vote (#445) or a QDevice |

## 4. Design

### 4.1 Detection (the leader)

Every 10 s, the leader looks at each member that carries a protected VM. A member is **lost** when its status
(`statd`, sent every 10 s) is older than `LOST_S` = 60 s, while the leader itself has the quorum. The time it was last
seen is the time of that status: it is what the fence decisions below count from, not the moment the leader noticed.

A leader without quorum is no leader (its lock lapses, `cluster_lead.renew`), so a node cut from the majority never
recovers anything: it is the one that fences itself.

### 4.2 Fence, then confirm

A lost member is **fenced** when one of these holds, checked in this order:

1. **BMC or AMT** (`ha_fencing`, method `ipmi`, `redfish` or `amt`): the leader runs the agent with `--action=off`,
   then polls `--action=status` every 5 s until it says off, for at most 60 s. Never `reboot`: a node that comes back
   on by itself before its VMs are restarted elsewhere is exactly the double start this prevents. The agent's output is
   in the audit entry; its password never is.
2. **Watchdog**: the member published `armed` while it was still seen, and `now − last seen > reset_within_s + 30 s`
   (`reset_within_s` is what the member measured of its own watchdog: 120 s for Intel's iTCO with a 60 s timeout, about
   60 s for softdog). That is the "about 2 minutes".

A BMC that fails or does not confirm does not stop the watchdog path: either proof is enough. With neither (no fencing
method, watchdog not armed or `HYPERLITE_WATCHDOG=0`), the member's HA VMs go to **manual**, with the reason.

### 4.3 Recovery

For each protected VM of the fenced member, one by one:

1. **Target**: among the online members (status fresh), not in maintenance, whose shared pool of the VM's disks is
   active there, and which pass `cluster_compat` for the stored definition (CPU model, firmware): the first of the VM's
   **preferred nodes** in their order; else the one with the **most free memory**, enough for the VM. Each member adds
   its free memory to its status (`statd`, one number), so the leader does not ask each one.
2. **Restart**: the leader asks the target, in the cluster's name (`?node=`, `peer_auth`), to define the VM from its
   stored definition and start it. The VM's HA record then names the target. A VM that does not start there is tried on
   the next candidate once.
3. **Limit**: at most 3 automatic restarts of a VM per hour; then it goes to **manual**.

### 4.4 The node that comes back

It was powered off or reset: its libvirt still has the old definition of each VM that moved.

- At its start, before anything starts at boot, a node **undefines** each VM whose HA record names another member
  (the disks are shared: only the definition goes), and audits it.
- An HA VM never has libvirt's own autostart (`virsh autostart`): only Hyperlite's start at boot, which checks the HA
  record first. A VM protected with libvirt's autostart set gets it turned off at protection.

### 4.5 States, audit, notifications

Each protected VM has a state in its replicated record: `ok`, `lost` (its node is lost), `fencing`, `recovering`,
`manual` (with the reason), and its last automatic restarts. Each change is an audit entry and a notification through
the existing channels (`ha_lost`, `ha_fenced`, `ha_restarted`, `ha_manual`).

### 4.6 Two nodes

With the primary node's second vote (#445), the primary keeps the quorum alone: it recovers the other node's HA VMs.
The other node, alone, has no quorum: when the primary is lost, nothing is restarted automatically, and the dashboard
says so. A QDevice makes both directions work, as on Proxmox.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Double start on the shared disk | Restart only after a confirmed fence (power off confirmed, or watchdog delay passed); the leader acts only with the quorum; the returning node undefines moved VMs before anything starts |
| The watchdog does not reset a hung node | Hardware watchdog where there is one; softdog otherwise, which a hard kernel hang can stop: stated in the UI next to the node's watchdog type |
| BMC unreachable in the failure that matters | The watchdog path still applies; with neither, the VM goes to manual. Unavailability, never corruption |
| A flapping network restarts VMs | 60 s before lost, about 2 minutes before a watchdog fence; 3 restarts per hour per VM |
| A fenced node's VMs left with nowhere to go | Manual state with the reason (no candidate, not enough memory, pool not active) |
| BMC credentials | Encrypted at rest, admin only, never returned, passed to the agent on standard input |

## 6. Steps (one pull request each, each tested in the lab)

1. ~~Watchdog self-fence~~ (merged, #444, #459).
2. **BMC/AMT fence**: `off` then `status` until off, in `ha_fencing`; tested with fake agents, and in the lab against
   the lab VMs through a virtual BMC (`vbmc`, IPMI to libvirt on the host).
3. **Recovery by the leader**: detection, fence decision, target, restart, limit, the returning node, states, audit
   and notifications; replaces `ha_watch`'s dry run. Lab: three nodes on an NFS share, an HA VM on node 2, node 2 cut
   from the network (`lab/vm.sh cut`): it resets within its watchdog delay, a single copy of the VM starts elsewhere,
   its disk intact, and node 2 back undefines it. The same with the virtual BMC.
4. **UI**: HA state per VM, preferred nodes, each node's fence method and watchdog state, history.

## 7. Questions for the maintainer

1. **Preferred nodes**: an ordered list per VM (as above), or also a "only these nodes" restriction?
2. **The returning node** undefines moved VMs automatically (4.4): agreed, or keep them defined and stopped?
3. **3 restarts per hour per VM**, then manual: agreed?
4. **`fence-agents`** (about 30 MB): a recommended package of Hyperlite, or left for the administrator to install when
   a node has a BMC?
