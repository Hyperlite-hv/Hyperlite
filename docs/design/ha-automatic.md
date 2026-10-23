# Design: automatic HA restart with reliable fencing

Status: **the maintainer's decisions of 2026-10-17 (#333) applied to the cluster, and what vSphere HA has beyond them
(disk locks, restart priority, admission control, VM monitoring, asked for on 2026-10-23); accepted on 2026-10-23,
with the answers of section 8.** Step 1 (the watchdog self-fence) is merged (#444, #459); steps 2 to 7 follow, one pull
request each.

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

For each protected VM of the fenced member, one by one, by **restart priority** (`high`, `medium`, `low`, as vSphere's
VM restart priority; `medium` by default), and in each priority in the order they were protected:

1. **Target**: among the online members (status fresh), not in maintenance, whose shared pool of the VM's disks is
   active there, and which pass `cluster_compat` for the stored definition (CPU model, firmware): the first of the VM's
   **preferred nodes** in their order; else the one with the **most free memory**, enough for the VM. Each member adds
   its free memory to its status (`statd`, one number), so the leader does not ask each one.
2. **Restart**: the leader asks the target, in the cluster's name (`?node=`, `peer_auth`), to define the VM from its
   stored definition and start it. The VM's HA record then names the target. A VM that does not start there is tried on
   the next candidate once. A start refused because its disk is still locked (4.7) is tried again every 10 s for the
   storage's lock lease (90 s on NFSv4), plus 30 s: a node powered off by its BMC may still hold it that long.
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

### 4.7 Disk locks on the shared storage (every VM, not only HA)

The fences above prove a node stopped; a lock on the disk makes a second copy **impossible** even if a proof was wrong
(a softdog stopped by a hung kernel, a bug), as vSphere's locks on VMFS and NFS do.

**QEMU's own image locks already do it** on a share that locks across nodes: the lab (three nodes, an NFS 4.2 share)
refused a disk that another node ran ("Is another process using the image?"), for every VM, with no libvirt lock
manager; released it once the VM stopped; and, the node killed hard, released it 94 s later, when the server's NFSv4
lease expired. So libvirt's `virtlockd` is not added: it would restart libvirt on every node for a second barrier of
the same kind. What Hyperlite makes sure of:

- **The share locks across nodes**: a lock on an NFS mount with `nolock` or `local_lock=` other than `none`, or on an
  SMB mount with `nobrl`, stays on the node that took it, and QEMU's lock then protects nothing between nodes. Each
  node checks the mounts of its shared pools (its preflight, the pool list): a pool whose mount does not lock across
  nodes cannot hold HA VMs, and the dashboard says why.
- **QEMU's locks are never turned off**: no definition Hyperlite writes sets `locking=off` on a disk, and turning on HA
  refuses a VM whose definition does.
- 4.3 waits for the storage's lease (90 s on NFSv4, plus 30 s) before it counts a start refused for a locked disk as a
  failure.

### 4.8 Admission control

As vSphere's admission control, so that the loss of a node does not leave HA VMs with nowhere to go: turning HA on for a
VM (or starting an HA VM) checks that, for each member, its HA VMs would fit in the free memory of the other online
members (the same placement as 4.3). Each member publishes its free memory in its status (4.3). The policy is per
cluster: `strict` refuses with the member that could not be absorbed; `warn` (the default) accepts and says so on the
VM and the cluster page; `off`.

### 4.9 VM monitoring

As vSphere's VM monitoring, per VM and off by default: a running HA VM whose guest agent answered before stops
answering QEMU's `guest-ping` for 3 rounds of 30 s; its node resets it in place (`virsh reset`), at most 3 times an
hour, then reports it. A VM without the guest agent is never reset: the option says it needs `qemu-guest-agent` in the
guest. It is the node's own work, with no failover: the VM stays where it is.

### 4.10 Compared with vSphere HA and Proxmox VE HA

| | vSphere HA | Proxmox VE HA | Hyperlite |
|---|---|---|---|
| Who decides | an elected primary host (FDM) | the cluster manager, with quorum | the leader, with quorum |
| A host that is lost | network and datastore heartbeats | Corosync quorum | status every 10 s, lost after 60 s |
| Proof the old copy stopped | locks on the datastore | watchdog self-fence | watchdog self-fence, or the BMC/AMT powered it off |
| Second barrier | the same locks | QEMU's image locks | QEMU's image locks, on shares checked to lock across nodes |
| Restart order | VM restart priority | through HA groups | restart priority |
| Capacity kept for a failure | admission control | no | admission control (`warn` by default) |
| A hung guest | VM monitoring (VMware Tools) | no | VM monitoring (QEMU guest agent) |
| Where | free capacity, DRS | HA groups, preferred nodes | preferred nodes, else the most free memory |

## 5. Risks

| Risk | Mitigation |
|---|---|
| Double start on the shared disk | Restart only after a confirmed fence (power off confirmed, or watchdog delay passed); the leader acts only with the quorum; the returning node undefines moved VMs before anything starts; and QEMU refuses a disk another node holds |
| A storage that does not lock across nodes | Each node checks its shared pools' mount options; such a pool cannot hold HA VMs |
| Admission control refusing too much | `warn` by default; `strict` only when the administrator asks for it |
| VM monitoring resetting a healthy guest | Only a VM whose agent answered before, 3 missed rounds of 30 s, 3 resets an hour, off by default |
| The watchdog does not reset a hung node | Hardware watchdog where there is one; softdog otherwise, which a hard kernel hang can stop: stated in the UI next to the node's watchdog type |
| BMC unreachable in the failure that matters | The watchdog path still applies; with neither, the VM goes to manual. Unavailability, never corruption |
| A flapping network restarts VMs | 60 s before lost, about 2 minutes before a watchdog fence; 3 restarts per hour per VM |
| A fenced node's VMs left with nowhere to go | Manual state with the reason (no candidate, not enough memory, pool not active) |
| BMC credentials | Encrypted at rest, admin only, never returned, passed to the agent on standard input |

## 6. Steps (one pull request each, each tested in the lab)

1. ~~Watchdog self-fence~~ (merged, #444, #459).
2. **BMC fence**: `off` then `status` until off, in `ha_fencing`; tested with fake agents, and in the lab against
   the lab VMs through a virtual BMC (`vbmc`, IPMI to libvirt on the host). The manual recovery uses it.
   2b. **AMT of Hyperlite's own**: Debian 13 packages no `fence_amt_ws` (its WS-MAN library is not in Debian), and
   `fence_amt` (amtterm) speaks the older interface that AMT 11 and later dropped. Hyperlite powers an AMT PC off
   itself, over WS-MAN (`CIM_PowerManagementService.RequestPowerStateChange`, digest authentication, port 16993 in
   TLS), and reads `CIM_AssociatedPowerManagementService.PowerState` until off.
3. **Disk locks** (4.7): each node checks that the mounts of its shared pools lock across nodes; HA refuses a pool that
   does not, and a definition with QEMU's locks turned off. Lab: a disk on the NFS share run on node 2, refused on
   node 3; the same share mounted with `local_lock=all`, not refused, and reported.
4. **Recovery by the leader**, with the restart priority: detection, fence decision, target, restart, limit, the
   returning node, states, audit and notifications; replaces `ha_watch`'s dry run. Lab: three nodes on an NFS share,
   HA VMs on node 2, node 2 cut from the network (`lab/vm.sh cut`): it resets within its watchdog delay, a single copy
   of each VM starts elsewhere in priority order, its disk intact, and node 2 back undefines them. The same with the
   virtual BMC.
5. **Admission control** (4.8), with each member's free memory in its status.
6. **VM monitoring** (4.9).
7. **UI**: HA state per VM, preferred nodes, priority, the admission policy, each node's fence method and watchdog
   state, history.

## 7. Questions asked

1. **Preferred nodes**: an ordered list per VM (as above), or also a "only these nodes" restriction?
2. **The returning node** undefines moved VMs automatically (4.4): agreed, or keep them defined and stopped?
3. **3 restarts per hour per VM**, then manual: agreed?
4. **`fence-agents`** (about 30 MB): a recommended package of Hyperlite, or left for the administrator to install when
   a node has a BMC?
5. **Disk locks for every VM on a shared pool** (4.7), not only HA VMs: agreed? It also stops a VM started by hand on
   the wrong node.
6. **Admission control**: `warn` by default (as proposed), or `strict` as vSphere's default?

## 8. Decisions (maintainer, 2026-10-23)

The maintainer accepted the design and left these to the proposals above:

1. Preferred nodes: an ordered list per VM, no "only these nodes" restriction for now.
2. The returning node undefines the VMs that moved, automatically, and audits it.
3. 3 automatic restarts per VM per hour, then manual.
4. `fence-agents` is a recommended package of Hyperlite (installed by default, removable).
5. Disk locks for every VM on a shared pool, not only HA VMs.
6. Admission control `warn` by default.
