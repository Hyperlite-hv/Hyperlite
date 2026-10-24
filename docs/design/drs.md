# Design: load balancing of a cluster (DRS)

Status: **proposed, for the maintainer to accept (#334)**. No code before that.

## 1. Goal

When a member of a cluster stays loaded above the others for a while, live-migrate a few VMs to even the load, as
vSphere DRS does: proposed to the administrator, or applied on its own, as the administrator chooses. Rules keep VMs
together or apart, or on some nodes. Nothing predictive at first: loads that last, and moves whose gain outweighs
their cost.

Out of scope: storage balancing (vSphere's Storage DRS), power management (DPM: emptying a node to switch it off),
prediction (#347), VMs whose disks are not on a shared pool (a migration that copies the disks takes minutes to hours:
proposed in manual mode only, never applied on its own).

## 2. What exists

| Piece | State | Where |
|---|---|---|
| Each member sends its status every 10 s: its VMs, its free memory | done | `app/core/statd.py` |
| Host CPU and memory, VM CPU and memory, sampled every 30 s, kept as history | done (per node, not shared) | `app/core/metrics.py` |
| Live migration with shared storage, and the check that a VM can go to a node (CPU, same name, iSCSI) | done | `app/routers/vms/migration.py`, `maintenance._why_not_migrable` |
| A node's drain: maintenance, then every running VM migrated to one target | done | `rolling_update._drain`, `routers/nodes.py` |
| One leader for the cluster-wide work, only with the quorum | done | `app/core/cluster_lead.py` |
| HA placement: preferred nodes, then most free memory; admission control | done | `app/core/ha_recovery.py`, `app/core/ha_admission.py` |
| A VM's operations held by a lock (backup, migration, snapshot) | done | `app/core/vm_locks.py` |

## 3. Design

### 3.1 Load, read from the statuses

Every status (statd, every 10 s) gains the node's CPU use (%), its memory (used and total) and, per running VM, its
CPU use and its memory. The leader then sees the whole cluster without asking anyone, as HA does.

A node's load is its CPU use and its memory use, each as a share of the node: `cpu` = used / total over the last
5 minutes (mean of the statuses), `mem` = used / total now. Its **load score** is the larger of the two: a node short of
memory is as loaded as one short of CPU.

### 3.2 When the cluster is unbalanced

The leader evaluates every 5 minutes. The cluster is **unbalanced** when the spread of the load scores (the highest
minus the lowest, among the members that may receive VMs) stays above the **threshold** for 3 evaluations in a row
(15 minutes): a burst is no imbalance. The threshold is one setting with five levels, as vSphere's migration threshold:

| Level | Spread that counts | For |
|---|---|---|
| 1, conservative | never: rules only | a cluster that must not move |
| 2 | 40 points | few moves |
| 3, default | 25 points | most clusters |
| 4 | 15 points | |
| 5, aggressive | 10 points | an even cluster, many moves |

At every level, a member above 90 % memory or CPU for 3 evaluations is unbalanced, whatever the spread, and a rule
broken (3.4) is always a reason to move.

### 3.3 Which moves

A greedy search, as vSphere's: from the most loaded member, the move of one VM to another member that lowers the spread
the most, then again, at most `max_moves` (default 4) per evaluation. A VM is a candidate when:

- it runs, all its disks on a shared pool (shared file pool or RBD; never a disk copy in automatic mode);
- nothing else works on it (`vm_locks`), it is no HA VM being recovered, it was not moved by DRS in the last hour
  (no ping-pong), and it is not excluded (3.5);
- the target is online, not in maintenance, can run it (`_why_not_migrable`: CPU, name), and has its memory free with a
  margin of 10 %;
- the move breaks no "must" rule, and admission control (HA) still holds after it.

A move is kept only if it lowers the spread by at least a quarter of the threshold: a migration costs (its memory is
copied over the network, the VM pauses briefly), and a small gain is not worth it. Among moves of equal gain, the VM
with the least memory first (the shortest migration).

### 3.4 Rules

Stored in the replicated configuration, shown and edited on the page:

| Rule | Meaning | "must" | "should" |
|---|---|---|---|
| Together (affinity) | the VMs of the rule run on one node | never split by DRS or HA | DRS brings them together when it can |
| Apart (anti-affinity) | the VMs of the rule run on different nodes | DRS and HA never put two on one node | DRS separates them when it can |
| On these nodes | the VMs run on the nodes listed | DRS and HA never put them elsewhere | DRS prefers those nodes |
| Not on these nodes | the VMs never run on the nodes listed | DRS and HA never put them there | DRS avoids them |

The "must" rules also bind HA (`ha_recovery.candidates`): a VM with no node left that keeps its rules goes to manual,
said why. A start or a migration by an administrator that breaks a "must" rule is refused, with the rule named; a
"should" rule only warns.

### 3.5 Modes

One mode for the cluster, and a per-VM exception:

- **Off**: nothing evaluated.
- **Manual** (the default): the page lists the recommended moves, each with its gain and its reason; an administrator
  applies one, or all.
- **Automatic**: the leader applies them, at most 2 migrations at a time in the cluster and 1 per node, and says each
  one in the audit log, the history and a notification.

A VM can be excluded, or kept in manual while the cluster is automatic (a database that must never move on its own).

### 3.6 Draining a node, and starting a VM

The drain (maintenance) stops sending every VM to one target: each VM goes where DRS would put it, its rules kept.
When a VM is created or started with no node named ("auto"), the same placement chooses its node (initial placement).

### 3.7 Safety

- Only the leader evaluates and moves, only with the quorum; nothing during a rolling update or while HA recovers a
  member.
- A migration that fails is not tried again for that VM for an hour; two failures in a row stop automatic mode, said
  in a notification.
- Every move goes through the existing live migration, with its checks; DRS adds no other way to move a VM.

## 4. Compared with vSphere DRS and Proxmox VE

| | vSphere DRS | Proxmox VE | Hyperlite (this design) |
|---|---|---|---|
| Balancing on its own | 🟢 levels, manual to automatic | 🔴 none (CRS places HA VMs only; ProxLB is a third-party tool) | 🟢 off, manual, automatic, threshold 1-5 |
| Affinity / anti-affinity | 🟢 VM-VM and VM-host rules, must and should | 🟠 HA groups only (node preference), affinity in 9.x | 🟢 the four rules, must and should, binding HA too |
| Drain spreading the VMs | 🟢 | 🟠 one target, or HA's choice | 🟢 each VM where DRS puts it |
| Initial placement | 🟢 | 🔴 the node chosen by hand | 🟢 "auto" |
| Cost of a move | 🟢 weighed | — | 🟢 a gain threshold, the smallest VMs first |
| Predictive | 🟢 with vRealize | 🔴 | 🔴 not at first (#347) |
| Licence | 🔴 Enterprise Plus only | 🟢 free | 🟢 free |

## 5. Risks

| Risk | Mitigation |
|---|---|
| Moves that never end (ping-pong) | 15 minutes of imbalance before a move, a gain threshold, one hour before a VM moves again |
| A migration hurting a busy VM | Manual by default; at most 2 at a time; the smallest VMs first; exclusion per VM |
| A "must" rule leaving HA nowhere to restart a VM | The VM goes to manual, said why; the page warns when a rule leaves a VM a single node |
| Loads read from statuses 10 s old | 5-minute means and 15 minutes of imbalance: a stale status changes nothing |
| A node of an older version in the cluster | Its status has no load: it is neither a source nor a target, said on the page |

## 6. Steps (one pull request each)

1. **Load in the statuses**: node CPU and memory, VM CPU and memory (3.1).
2. **Rules and settings**: replicated tables, API, checks on start and migration (3.4); "must" rules in HA placement.
3. **Recommendations**: the evaluation on the leader, manual mode, the page with its list and "Apply" (3.2, 3.3, 3.5).
4. **Automatic mode** and the per-VM exceptions (3.5, 3.7). Lab: three nodes, VMs loaded on one, moved until even.
5. **Drain and initial placement** through DRS (3.6).

## 7. Questions for the maintainer

1. **Default mode**: manual (proposed), or automatic as vSphere's default?
2. **Threshold**: level 3 (25 points) by default?
3. **VMs on local disks**: proposed in manual mode with their disk copy (minutes), or never?
4. **"Must" rules binding HA**: agreed that a VM with no node left keeping its rules is not restarted (it goes to
   manual)? vSphere does the same with "must" rules.
5. **Initial placement**: should "auto" become the default node when a VM is created in a cluster?
