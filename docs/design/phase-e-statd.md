# Phase E: the cluster's status, published by every node (`hyperlite-statd`)

Status: **design, for review**. No code before Antho's approval. It details section 4.5 of
`docs/design/hyperlite-cfs.md` (accepted on 2026-10-01) with what the scale test of 2026-10-18 measured
(`docs/scale-results.md`).

## Why

After the quick fixes (#476, #477), a member's VM list comes from that member in one HTTPS call. That still grows with
the cluster: the dashboard asks every node in turn (6 at a time in a browser), so 100 nodes are 17 rounds of a call,
and every node reads its own VMs again whenever its 2-second cache has expired (the remaining p95 of 200-280 ms on the
lab, CPU saturated under 20 users). Proxmox does not ask: `pvestatd` on every node "queries the status of VMs,
storages and containers at regular intervals. The result is sent to all nodes in the cluster", and every page is
answered from that.

## What

1. **Every node reads its own state once per period.** One bulk libvirt call (`getAllDomainStats`, already what the
   inventory scan uses) plus the definitions that changed, never the 333 definitions each time. The summary of each VM
   is what the VM list shows: name, state, vCPU, memory, CPU %, memory used, IP, uptime, firmware, tags flags.
2. **It sends the result to every node**, as one compressed message, every **10 s** and at once on a libvirt event
   (a VM started, stopped, defined, deleted). Only what changed since the last message is sent, with a full message
   every minute so a node that missed one catches up.
3. **Every node keeps every node's latest status in memory** (never on disk: it is rebuilt within 10 s of a start), with
   the time it was received.
4. **The list pages read that table.** The dashboard's VM list becomes one request to the node it is open on: every
   node's VMs, each with its node and the age of its figures. Nothing is asked of the other nodes on a request.
5. **A node that stops sending** is shown with its last figures marked stale after 30 s, then as not answering, as
   today's `unreachable` lists do.

### The channel

The messages go through **hyperlite-cfs**, on a second Corosync group (CPG) next to the configuration's: the daemon is
already connected, authenticated and encrypted by Corosync, and knows the members. They are **not** part of the
replicated tree: not ordered with the configuration's writes, not written to SQLite, never subject to the quorum (a
node cut off still shows its own VMs, marked as such). The Python service sends its summary to the daemon over the
existing socket and reads the table from it, as it does for the tree.

Size: about 150 bytes per VM compressed, so 50 KB per node of 333 VMs in a full message, a few hundred bytes in a
change message. 100 nodes of 10 VMs: under 2 KB per node every 10 s.

### What stays as it is

- Actions (start, stop, edit) still go to the node that runs the VM (`forward_to`).
- A VM's own page still reads it live from its node: only the lists use the table.
- Outside a cluster: the table holds this node only, filled the same way; the list reads it.

## Thresholds (docs/design/scale-test.md)

- Dashboard VM list, 1,000 VMs, any number of nodes: **p95 under 200 ms** (target: under 50 ms).
- 20 users at once: p95 under 300 ms, no error.
- A change of state (a VM started) visible on every node within 2 s (the event), the figures within 10 s.

## Tests

- C (cfs): the second group delivers to every member, a member that leaves drops out of the table, messages over the
  size of one Corosync message are split, fuzzing of the decoder (as for the tree's messages).
- Python: the summary, the change messages, the staleness, the list from the table.
- Lab: `lab/scale/scale.py` again with 1,001 VMs, 20 users; a node cut off (`lablink.py`) shown stale then
  unreachable; the cfs fault tests of phase B still pass.

## Order of work

1. The daemon's status group and its socket commands (C), with its tests and fuzzing.
2. The Python sender (the node's summary every 10 s and on libvirt events) and the table reader.
3. `GET /vms?cluster=true` from the table, the dashboard on it; then the other lists (containers, storage).
4. The scale test again; the old per-node calls kept one version for a mixed cluster during a rolling update.

## Open questions

1. The period: **10 s** as Proxmox (and the accepted cfs design), or shorter (5 s) for fresher CPU and memory figures
   at a little more network traffic?
2. During a rolling update, nodes of the old version send no status: their VMs listed the old way (one call per node)
   until they are updated. Agreed?
