# Several clusters and sites from one view (#656)

Status: **accepted (maintainer, 2026-10-25).** Section 2 holds the maintainer's choices.
Step 1 merged (PR #717).

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
2. **Actions** on a remote's VMs.
3. **The backup copies' remotes** moved into the shared list (after #511's move of the backups).
4. **Offline migration** between clusters.
5. **Live migration** between clusters, where both reach the same storage or the disks can be copied while it runs.

## 8. Risks

| Risk | Mitigation |
|---|---|
| A slow or cut remote slows the page | Every remote asked in parallel with a timeout; one that does not answer is named, the others shown |
| A token that can do too much | Tokens by use, each of a dedicated account whose rights the remote's administrator sets; the view never needs an administrator's token |
| Two clusters of different versions | Every call names API v1; a remote whose version lacks a route (`/sites/summary` before this step) is shown as not answering, with the reason, until it is updated |
| The view as a target | It holds tokens, not keys: their rights on each remote are its administrator's choice, and every call is in the remote's audit log |
