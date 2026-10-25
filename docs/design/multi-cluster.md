# Several clusters and sites from one view (#656)

Status: **accepted (maintainer, 2026-10-25).** Section 2 holds the maintainer's choices.
Steps 1 (PR #717) and 2 merged.

## 1. Why

A cluster stays within one site (Corosync needs LAN latencies, about 32 nodes at most). A company with several sites,
or more than one cluster, today opens one dashboard per cluster, and a site across a WAN can only be a host driven over
SSH from one node (deprecated since 2026-10-24: no management when the link is cut, an SSH root key on every host).

Proxmox answers with Proxmox Datacenter Manager (PDM), a separate installation that reaches every cluster and single
node through their API; each site keeps running on its own when PDM or the link is gone. vSphere's vCenter manages the
hosts of every site, but they depend on it: without vCenter there is no HA change, no DRS, no management.

## 2. Decisions (maintainer, 2026-10-25)

| Question | Proxmox | vSphere | Hyperlite |
|---|---|---|---|
| Where the view runs | PDM, a separate installation | vCenter, a separate appliance the hosts depend on | **In every Hyperlite** (chosen as the best for companies): any cluster can be the view of the others, on a page of its own dashboard, with no server to add. Its remotes are in the replicated configuration, so every node of that cluster serves the view: it is never a single point of failure. A company may dedicate a small cluster without VMs to it, which is still a plain Hyperlite |
| First version | PDM 1.0: state, basic actions, migration between remotes | All of it, through vCenter | **State and basic actions, then migration** between clusters (offline first, then live where the storage allows), as `qm remote-migrate` |
| Remotes | PDM's remotes, PBS's remotes | (hosts joined to vCenter) | **One list of remote clusters**, each declared once, with a token per use and the least rights for each: the backup copies' remotes (#699) join it once #511 moved the backups' code |
| Actions (step 2) | PDM: power actions and migration inside a remote; the console and the rest in the remote's own interface | vCenter: everything, the console included, from vCenter | **As PDM** (the best of the two for a view that holds only tokens): start, shutdown, reboot, force stop, reset, and a move to another node of the same remote; the console and the settings in the remote's own dashboard, one click away. A console through the view would need a tunnel between clusters: more code and more to attack for little |
| The token of the actions | PDM: a token per remote, its rights set on the remote | (vCenter's own accounts) | **A second, optional `manage` token** of another dedicated account, whose role the remote's administrator chooses (Operator, or Administrator to move VMs); without it the view is read-only and shows no action |

## 3. Remotes

A **remote** is another Hyperlite cluster (or a single node), as PDM's remotes are:

- **Several endpoints**: the address of each of its nodes with the SHA-256 of that node's HTTPS certificate, pinned
  (refused before anything is sent when it does not match). The administrator adds one endpoint, compares its
  fingerprint with the one the other dashboard shows (as for the backup remotes, #702), and the view reads the
  remote's members, their addresses and their fingerprints from it (`GET /sites/fingerprints`, which a viewer may read;
  `GET /cluster` is an administrator's): a call
  goes to the first endpoint that answers, so a remote node that is down hides nothing.
- **A token per use**, each of a dedicated account on the remote with the least rights, kept encrypted in the cluster's
  secrets (a table under `/priv`): `view` (a global viewer: state only), `manage` (the roles the remote's administrator
  chooses on its pools: start, stop, migrate there), `backup` (`backup_store.read` on the stores to copy, #699). The
  remote checks every call with its own permissions and records it in its own audit log; the view records it too.
- **Tables** (replicated): `remotes` (name, kind, added by, at), `remote_endpoints` (remote, address, port,
  fingerprint), `remote_tokens` (remote, use, token encrypted; under `/priv`). `backup_remotes` (#699) is copied into
  them by a migration in the step that merges the backups' remotes, its copy jobs then naming the same remote.

## 4. What the view shows and does

A **Sites** page in the dashboard (Datacenter › Sites), for administrators:

- **Every remote**, with its state (reachable, each node up or down, its quorum, its version), its counts (nodes, VMs
  running and stopped, alerts) and its capacity (CPU, memory, storage used and free), and this cluster itself, so the
  page is the whole estate.
- **One remote**: its nodes, its VMs (the list the remote's `GET /vms?cluster=true` gives, with the node of each),
  its storage pools, its active alerts; a search across every remote by VM name, tag or address.
- **Actions** (step 2): start, shut down, stop, restart a VM, on the remote that holds it, with the `manage` token: the
  request is the remote's own route (`POST /vms/{name}/start?node=...`), so its checks, locks, tasks and audit apply;
  the view follows the task it answers.
- **Migration between clusters** (steps 4 and 5): a VM moved from one remote to another (or to this cluster), its
  disks copied and its definition remade there, the source removed once the copy runs; offline first, then live.

**How it reads**: on demand, every remote at once (a bounded pool of connections), each call with a timeout, the
answers kept 15 s so a page opened by several administrators asks once; a remote that does not answer in time is
named, never waited for. Nothing of a remote is stored here but its remote's definition.

## 5. Scale

A view over a hundred clusters: the summary of each is one call (a new `GET /sites/summary` on every Hyperlite,
counts and capacity in one answer, from what the status channel already holds), so the Sites page is one call per
remote, in parallel. The lists of one remote are its own (paginated) lists.

## 6. Security

- Pinned certificates, never SSH; tokens of dedicated accounts with the least rights, encrypted at rest, never answered
  back; every action checked and recorded by the remote, and recorded here too.
- A remote never reaches the view: the view calls, the remotes answer. A view cluster that is compromised can do on a
  remote what its tokens allow there, and no more: the remote's administrator chooses those rights.

## 7. Steps

Each one ships with its tests and the docs:

1. **Remotes** (endpoints, pinned fingerprints, members read from the first, tokens by use) and the **Sites page**
   read-only (`GET /sites/summary` and `GET /sites/fingerprints` on every node; the view's own `GET /sites`, `GET /sites/{remote}/...`).
2. **Actions** on a remote's VMs (`POST /sites/{remote}/vms/{vm}/{action}` and `.../migrate`, with the `manage`
   token): sent first to the node the VM runs on, else through another member with `?node=`; an action that reached
   the remote is never sent again to another endpoint.
3. **The backup copies' remotes** moved into the shared list (after #511's move of the backups).
4. **Offline migration** between clusters (section 9.1).
5. **Live migration** between clusters (section 9.2).

## 9. Migration between clusters (steps 4 and 5)

Status: **proposal** (2026-10-25). The choices below are the maintainer's, from the questions asked the same day; no
code before this section is accepted.

| Question | Proxmox | vSphere | Hyperlite |
|---|---|---|---|
| How the disks go | `qm remote-migrate`: the source streams them to the target through a tunnel over the target's API, with its token | Cross vCenter vMotion: host to host, the vCenters agreeing | **A direct stream** from the source cluster to the target node, over pinned HTTPS with the target's `manage` token: the same path the live migration (step 5) will use, and nothing of the backups' (#511) is needed |
| The VM left at the source | Kept, locked, unless `--delete` | Gone: a moved VM exists in one place | **Deleted** once the target has the VM and its disks checked: never two copies that could start with the same MAC address |
| Storage and network on arrival | `--target-storage`, `--target-bridge` mappings, per migration | Chosen in the wizard, per migration | **Per migration**: for each disk a pool of the target, for each card a network of the target, the same name chosen first when it exists; nothing saved |

### 9.1 The flow (offline, step 4)

The view is the source cluster; the target is one of its remotes, with a `manage` token of an account that holds the
Administrator role there (a migration creates a VM and its volumes, as `qm remote-migrate` needs `VM.Allocate` and
`Datastore.AllocateSpace`).

1. **The dialog** (the VM's page, *Move to another cluster*): the remote, its node (online ones), the name there (the
   same by default), each disk's pool (with its free space) and each card's network, *start it there afterwards*, and
   an optional bandwidth limit in MiB/s (`--bwlimit`).
2. **The source's checks**, answered at once: the VM is stopped (the dialog offers to shut it down first); it has no
   snapshot (refused, with how many: qcow2 internal snapshots are not carried in v1, and a VM is never moved with
   part of its history missing); no device of this host (PCI or USB passthrough, an iSCSI LUN of this site); not
   locked. What does not follow the VM is listed before the move: its HA protection, its backup schedule and its
   replication jobs here are removed with it; its backups stay in this cluster's stores (they restore here); an ISO in
   its CD-ROM is detached.
3. **The target prepares** (`POST /sites/incoming` on the chosen node, the `manage` token): the source sends the VM
   **in Hyperlite's own terms, never its libvirt XML** (CPU, memory, firmware and machine, the disks' sizes and
   formats, the cards' MAC addresses and models, the devices from the same allowlist as VM creation, tags and notes).
   The target validates it as it validates a VM it creates (a remote's XML could name any file of the target host),
   checks the name is free in the whole cluster, the pools have room, the networks exist, the CPU model is
   compatible (the same diagnostic as a migration inside a cluster) and its own version, creates the empty volumes
   (a hyperlite-daemon operation) and answers an incoming id. An incoming that sees no data for an hour is removed.
4. **The disks**, one after the other (`PUT /sites/incoming/{id}/disks/{n}` on that node, streamed): the source reads
   only what is allocated (the disk's allocation map, as the backups do), and sends frames of up to 4 MiB, each with
   its offset and length, compressed with zstd; the target checks every frame falls inside the disk it declared and
   writes it into the new volume; the stream ends with the SHA-256 of the whole disk (holes as zeros), which the
   target compares with its own. Progress is in a task at both ends. Both ends read and write the disks through
   hyperlite-daemon (a `qemu-nbd` export under `/run/hyperlite/migration/`, read-only at the source, writable at the
   target, opened and closed by the daemon: from #511's step 4 the API cannot open a disk itself).
5. **The target finishes** (`POST /sites/incoming/{id}/finish`): it defines the VM on that node with its new volumes,
   the mapped networks, the same MAC addresses and UUID, its tags and notes, and starts it if asked. It answers once
   the VM is defined.
6. **The source deletes** the VM, its disks and what was listed in point 2, and writes the move in its journal (the
   target writes it in its own).

**Failures.** Before point 5 answers: the source cancels the incoming (`DELETE /sites/incoming/{id}`: the target
removes its volumes), and the VM at the source is unlocked, untouched. After it answered but before point 6 is done:
the VM exists on both, the one at the source stays stopped and locked "moved to <remote>", and an alert says to
delete it there: it is never started by Hyperlite.

### 9.2 Live (step 5)

The same incoming and the same stream for the disks, while the VM runs: a first copy of every disk, then of the
blocks written since (a dirty bitmap per disk, as the incremental backups), until what is left is small; then libvirt
moves the memory and the device state through the same pinned connection (Proxmox's tunnel), the disks' last blocks
included, and the VM continues on the target. Its own section is written when step 4 is merged.

### 9.3 hyperlite-daemon

New operations, coordinated with #511 on #298 before any code: at the source, `migration.export` (a read-only export
of a stopped VM's disk, and its allocation map); at the target, `migration.prepare` (the empty volumes in the chosen
pools), `migration.import` (a writable export of one of them), `migration.define` (the VM from the checked
specification) and `migration.abort` (the volumes of an incoming removed). Each one takes fixed arguments, checked
again by the daemon.

### 9.4 Its risks

| Risk | Mitigation |
|---|---|
| A remote sends a VM that reaches the target host's files | The VM comes as a specification checked as a new VM, never as libvirt XML; every disk is a new volume of a pool the target chose |
| A stream that writes elsewhere or forever | Every frame inside the size declared at point 3; the stream ends with the SHA-256 the target checks; an idle incoming is removed after an hour |
| A cut link in the middle | Nothing at the source changes before the target answered point 5; the incoming is cancelled, or removed after an hour |
| The VM in two places | The source's copy stays stopped and locked until it is deleted; the target's is the only one Hyperlite starts |
| A slow WAN | Only allocated blocks, compressed; a bandwidth limit per migration; progress at both ends |

## 8. Risks

| Risk | Mitigation |
|---|---|
| A slow or cut remote slows the page | Every remote asked in parallel with a timeout; one that does not answer is named, the others shown |
| A token that can do too much | Tokens by use, each of a dedicated account whose rights the remote's administrator sets; the view never needs an administrator's token |
| Two clusters of different versions | Every call names API v1; a remote whose version lacks a route (`/sites/summary` before this step) is shown as not answering, with the reason, until it is updated |
| The view as a target | It holds tokens, not keys: their rights on each remote are its administrator's choice, and every call is in the remote's audit log |
