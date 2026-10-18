# Confirmed writes in a cluster

Status: **accepted** (Antho, 2026-10-18), implemented: `app/repositories/cfs/confirm.py`, `shadow.confirm()` and the
`confirm_writes` middleware (`app/main.py`). Decided by Antho on 2026-10-18 after a lab run (code review #448):
"écriture confirmée".

## What happens today

A request that changes the configuration (a group, a pool, a setting) writes this node's SQLite and answers at once.
A background loop then copies the change to hyperlite-cfs (`app/repositories/cfs/shadow.py`, `drain()`, every
second), with compare-and-set against the version this node last saw. Changes from other nodes arrive by another loop
(`inbound.apply()`, every 2 seconds).

Seen in the lab (`lab416.py`, check 1c): a group renamed on hlc2, then deleted on hlc3 one second later. hlc3 had not
received the rename yet, so its delete started from an older version. The API answered **200**; a second later the
copy loop found the conflict, dropped the delete ("the cluster's version wins", audit entry `cfs_conflict`) and the
group came back. The user was told it was done, and it was not.

Seen again in the lab (check 7a): hlc3 cut off at 23:34:13 answered **200** to a rename made in the same second,
because the write guard reads the quorum through a 2-second cache (`cluster_lead.QUORUM_TTL_S`); the daemon refused
to copy it, and the majority's value came back when the quorum did. A confirmed write sees the daemon refuse the copy
and answers 503 at once.

Proxmox's pmxcfs writes synchronously: a change that cannot be made is an error at once.

## What changes

For a request that changes something (`POST`, `PUT`, `PATCH`, `DELETE`), in a cluster only:

1. **Catch up first.** Before the handler runs, the node applies what the cluster changed and it has not applied yet
   (`inbound.apply()`, usually nothing, a few milliseconds). Most conflicts disappear: the handler sees the rename
   that came one second earlier.
2. **Copy before answering.** When the handler answers a success, the node copies the changes this request made
   (`drain()` of the outbox entries written during the request) before the answer leaves.
3. **A conflict is an error.** If the cluster changed one of those entries meanwhile, the answer becomes **409**:
   `{"detail": "Changed on another node meanwhile: your change was not applied. Reload and try again.", "code":
   "changed_elsewhere"}` (the code of the API v1 design). The cluster's values are applied back on this node, as
   today, and the audit keeps its `cfs_conflict` entry.
4. **Slow daemon.** If the copy does not finish within 15 seconds (the daemon is busy or stuck), the answer is **503**
   ("the cluster did not confirm the change in time; it will be copied when it answers") and the change stays in the
   outbox, copied by the loop as today. The Replicated configuration page already shows how many changes
   wait.

Outside a cluster (a single node, the daemon in local mode): nothing changes.

### What stays asynchronous

- Changes made by background work (a task, a scheduler, the HA watch): no one waits for an answer. Their conflicts are
  audited as today and, new, sent as a notification (event `cfs_conflict`) to the channels that subscribe to it.
- Changes a node makes while it starts (#465).

### Which changes are the request's

While a request runs, the application's connections (`ids.Connection`) begin each write transaction with `BEGIN
IMMEDIATE`: no other connection adds to the outbox until it ends, so every outbox id handed out meanwhile (the
outbox's AUTOINCREMENT counter before and after each statement) is the request's own. Nothing changes outside a
request.

Before the handler runs, the node first copies its own pending changes, then applies the cluster's (`inbound.apply()`
applies nothing while this node has changes waiting).

### Two drains at once

`drain()` is called by the loop and now by requests: it gets a lock, and a request drains only the entries its own
write produced (their outbox ids), so a request never waits for another one's changes.

## Cost

A change answers after one round trip to the local daemon, which already replicated it through Corosync (a few
milliseconds on a LAN, tens over Tailscale as between serveur-antho and bico). Reads are unchanged.

## Tests

- Unit: the fake cluster of `tests/test_cfs_inbound.py`: catch-up before the write (no conflict), a real conflict
  (409, the cluster's value back, audit entry), a slow daemon (503, the change pending, copied later), a single node
  (unchanged), a background write (asynchronous, notified).
- Lab: `lab416.py` gets a new check, the rename then delete one second apart: either it works (caught up) or it
  answers 409, never 200 followed by a revert. Run ten times in a row.

## Answered (Antho, 2026-10-18)

1. The limit before answering 503 is **15 seconds**, for clusters over a slow or distant link.
2. The `cfs_conflict` notification of a background change is **on by default** for the alert channels.
