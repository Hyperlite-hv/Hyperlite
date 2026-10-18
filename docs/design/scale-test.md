# Scale test: 1,000 VMs, 100 nodes

Status: **design, for review**. Asked by Antho on 2026-10-18 (code review #448, report: "la tenue à 1 000 VM n'est pas
mesurée"); both a real lab test and a simulation. Measure first: nothing is optimised before the numbers say where.

## Goals and thresholds

The exit criterion of phase E (`docs/design/hyperlite-cfs.md`, section 8) is **1,000 VMs listed in under 200 ms**. The
test measures every place where the number of VMs or nodes shows:

| What | Threshold (proposed) |
| --- | --- |
| VM list of the dashboard, 1,000 VMs over 3 nodes | p95 under 200 ms (phase E's criterion) |
| Overview, Activity (tasks and audit of every node), Cluster page | p95 under 1 s |
| 20 users at once, each refreshing the VM list every 5 s | no answer over 2 s, no error |
| Creating a VM with 1,000 others defined | under 5 s before the task starts |
| Replication of the configuration: 1,000 VM notes and tags changed on one node | every node has them within 30 s |
| The service's memory on a node with 333 VMs | under 500 MB, flat over one hour |
| A cluster of 100 nodes (simulated): Activity, Cluster page, a NAT address for a new VM | p95 under 2 s |

## Part 1: the real lab (hlc1-3)

- **1,000 VMs defined in libvirt**, 333 per node, created through Hyperlite's API like a user (so its records exist
  too), each with a tiny thin disk (a few KB used) and no operating system. Stopped: a defined VM costs no memory.
- **30 VMs running** (10 per node), 32 MB each, booting nothing (they sit in the firmware): enough for the live data
  (state, CPU, memory) that a running VM adds to every listing. The lab nodes have 2 GB each; they get 3 GB for the
  test (this host has 7 GB free, the Minecraft VM stays off).
- Load: `perf.sh` (latency of every list endpoint, min/median/p95/max), a small load generator (20 simulated users,
  Python threads), the service's memory and CPU sampled every 10 s, the cfs daemon's tree size and replication delay.
- Cleanup: the 1,000 VMs deleted through the API at the end, and the lab's disk snapshots taken before, to put it back
  exactly.

## Part 2: the simulation (100 nodes)

The lab cannot have 100 nodes. A test harness (`tests/scale/`, not run by CI by default: `pytest -m scale`) starts the
real application with:

- a fake libvirt per node (the fakes of the unit tests, grown: N VMs with states and statistics);
- `rolling_update.peer_api` (node-to-node calls over SSH) replaced by a call that waits a realistic delay: 30 ms on a
  LAN, 150 ms over Tailscale or a remote site, and a few nodes that do not answer (timeout);
- 100 nodes of 10 VMs, then 10 nodes of 100 VMs.

It measures the same pages and the code paths that grow with the number of nodes, read in the code during the review:

- the cluster views ask **8 nodes at a time** (`cluster_view.gather`): 100 nodes are 13 rounds of a node-to-node call;
- a new VM's NAT address asks **every node in turn** (`network_alloc.cluster_used_ips`);
- the node poller, the HA watch and the boot sequence, whose cost per round grows with nodes and VMs.

## Results and what follows

A report (`docs/scale-results.md` in the repository, the raw numbers in the PR): every measure against its threshold,
the slowest code paths with a profile (`py-spy` on the lab only, never on serveur-antho). Each threshold missed becomes
an issue, fixed in its own PR and measured again. Phase E's status channel (`hyperlite-statd`) is designed only if the
numbers show the listings need it.

## Order of work

1. The load generator and the simulation harness (a PR: tools and tests only).
2. The lab run, the report.
3. Issues and fixes, each measured again.

## Answered (Antho, 2026-10-18)

1. The thresholds above are the ones.
2. The lab nodes get 3 GB of memory for the test, back to 2 GB after.
