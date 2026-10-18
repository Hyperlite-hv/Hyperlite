# Scale test results (2026-10-18)

The test of `docs/design/scale-test.md`, run on branch `test` at 830060d (the clean package included). Tools:
`lab/scale/scale.py` (real lab) and `lab/scale/simulate.py` (simulation). Raw numbers in the pull request.

## Real lab: 3 nodes, 1,001 VMs

hlc1-3: QEMU VMs of 2 vCPU and 3 GB each on one host, a LAN between them. 333 VMs per node created through the API
(0 failures, 0.2 to 0.5 s each), 15 of them running.

| Measure | Result | Threshold | |
| --- | --- | --- | --- |
| VM list as the dashboard loads it | p50 241-276 ms, **p95 590-629 ms** | p95 < 200 ms | missed |
| This node's VMs alone (`GET /vms`) | p50 14-18 ms (cached), p95 180-195 ms (read again) | | |
| Activity and audit of every node | p95 247-274 ms | < 1 s | met |
| Dashboard, tags | p95 under 52 ms | < 1 s | met |
| 20 users refreshing the VM list every 5 s for 5 min | p50 1.85 s, **p95 2.06 s, max 2.25 s, 3 errors 500** | none over 2 s, no error | missed |
| Creating a VM with 1,000 others defined | 0.8 to 1.1 s | < 5 s | met |
| 333 notes and tags replicated to the other nodes | 9.1 s (about 27 s for 1,000) | < 30 s for 1,000 | met, barely |
| Service memory under load | 178-189 MB, flat; hyperlite-cfs 11 MB | < 500 MB | met |
| Load average under the 20 users | up to 6.8 on 2 vCPUs | | saturated |

The errors: `kex_exchange_identification: Connection reset by peer`. Every request about another node's VMs opens a
new SSH connection (`libvirt.open("qemu+ssh://...")`); with 20 users, more than 10 SSH handshakes start at once and
sshd drops the extra ones (`MaxStartups`, 10 by default). The request then fails with an unhandled 500.

## Simulation: up to 100 nodes

The real application in uvicorn; only the network edges are simulated with the lab's costs: 200 ms to open an SSH
connection, one round trip per remote libvirt call (0.5 ms on a LAN, 20 ms over Tailscale). Calibrated on the lab:
746 ms simulated for the VM list read again, 590-629 ms measured.

| Nodes x VMs, link | VM list | Activity | Audit | NAT address of a new VM |
| --- | --- | --- | --- | --- |
| 3 x 333, LAN | 0.75 s | 0.21 s | 0.24 s | 0.42 s |
| 3 x 333, Tailscale | **7.2 s** | 0.23 s | 0.23 s | 0.53 s |
| 10 x 100, LAN | 0.72 s | 0.41 s | 0.43 s | 1.8 s |
| 10 x 100, Tailscale | 4.8 s | 0.46 s | 0.47 s | 2.4 s |
| 100 x 10, LAN | **4.4 s** | **2.6 s** | **2.6 s** | **20 s** |
| 100 x 10, Tailscale | **8.6 s** | **2.9 s** | **2.9 s** | **26 s** |

## Why

1. **Another node's VMs are read through libvirt over SSH, one call per VM.** `GET /vms?node=X` opens
   `qemu+ssh://X` (200 ms) and reads every VM's definition with one round trip each: 333 VMs over Tailscale are
   333 x 20 ms, 7 s. The node itself has the same list in its inventory cache, read locally in 15 ms.
2. **Nothing reuses an SSH connection.** Every libvirt connection and every call to another member's API
   (`rolling_update.peer_api`) is a new SSH handshake, which costs 200 ms and CPU on both ends, and past 10 at once
   sshd refuses them.
3. **The NAT address of a new VM asks every node in turn** (`network_alloc.cluster_used_ips`): 100 nodes are 100
   sequential connections, 20 s.
4. **The cluster views ask 8 nodes at a time** (`cluster_view.gather`): 100 nodes are 13 rounds of a handshake.
5. **An unreachable node is a 500**: the libvirt error is not turned into an answer that names the node.

## Proposed fixes, by effect

| # | Fix | Expected |
| --- | --- | --- |
| 1 | Another member's VM list comes from that member's own Hyperlite (its inventory cache), in one call, instead of libvirt over SSH | 3 x 333 over Tailscale: 7.2 s to about 0.3 s |
| 2 | SSH connections reused (one per node, kept open a few minutes) for libvirt and member calls | about 200 ms less per call; no more dropped handshakes |
| 3 | The NAT address search asks the nodes at once, from their own records | 100 nodes: 20 s to under 1 s |
| 4 | Cluster views ask every node at once once connections are reused | 100 nodes: 2.6 s to under 0.5 s |
| 5 | A node that does not answer is named in the answer (502), the others still listed | no more 500 |
| 6 | Phase E: every node publishes its VMs' state to the cluster, any node lists them all from local data (as Proxmox's pvestatd does) | the VM list independent of the number of nodes, under 50 ms |

## What could not be tested

- Real hardware and a real 100-node cluster: the simulation stands for it, calibrated on the lab.
- More than 15 running VMs: the lab's memory.

## After the fixes (lab, 1,001 VMs, same tools)

| Measure | Before | Quick fixes (#476, #477) | Phase E (#479 and this) | Threshold |
| --- | --- | --- | --- | --- |
| Dashboard VM list, p50 | 241-276 ms | 69-74 ms | 34-35 ms | |
| Dashboard VM list, p95 | 590-629 ms | 196-276 ms | **37-42 ms** | < 200 ms (target 50) |
| Activity and audit of every node, p95 | 247-274 ms | 27-44 ms | 27-44 ms | < 1 s |
| 20 users for 2 min: p50 / p95 / max | 1.85 s / 2.06 s / 2.25 s | 0.85 s / 0.94 s / 1.6 s | **0.25 s / 0.31 s / 0.38 s** | none over 2 s |
| Errors under load | 3 | 0 | 0 | none |
| NAT address search, 100 simulated nodes | 20 s | 0.8 s | 0.8 s | < 2 s |

Phase E: every node sends a summary of its VMs to every node every 10 s through hyperlite-cfs's status group, and at
once after a change made through Hyperlite; the VM list reads it, this node's own VMs included (as Proxmox does,
Antho's choice), so nothing is asked of any node on a refresh. The last step was skipping FastAPI's generic encoder on
this list: walking a thousand summaries on every request was its largest cost under load.
