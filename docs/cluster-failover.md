# Taking over when the controller is lost

Hyperlite runs on one controller host; the other nodes are driven over SSH. If the controller host is lost, the VMs
on the other nodes keep running, but there is no dashboard, and nothing runs that needs the controller (HA watching,
scheduled backups, jobs, notifications). This page is how another node takes over. It is option (a) of the
[cluster configuration design](design/cluster-config.md).

## What is copied, and when

The controller copies its configuration to every online node:

- every 15 minutes, and within a minute after a configuration change (a user, a permission, a node, HA or fencing
  settings, a backup schedule, an API token, a security key...);
- as a bundle holding a consistent snapshot of the database **without the telemetry** (metrics), the two keys of
  `.env` (`HYPERLITE_SECRET_KEY`, `HYPERLITE_ENCRYPTION_KEY`) without which the copy could not read its own secrets,
  and a description (source controller, date);
- over the cluster SSH key (encrypted in transit) into `/var/lib/hyperlite/config-copy/` on the node: a root-only
  directory (0700), files 0600. `latest.tar.gz` is the last copy, `previous.tar.gz` the one before.

The Nodes page shows the last copy of each node and its error, if any, with **Copy now**.

What a promotion loses: the tasks and audit entries written since the last copy (at most 15 minutes, usually less),
and the metrics history. Configuration changes are copied within a minute, so they are not lost.

## Taking over

On the node that takes over, which must have the Hyperlite package installed:

```bash
/root/hyperlite/scripts/hyperlite-promote
```

The command:

1. shows which controller the copy comes from and how old it is;
2. **refuses while the old controller still answers** (its dashboard on port 8000, or SSH). Two controllers would
   both run HA, backups and jobs against the same VMs. Stop the old one first, or pass `--force` only when you are
   sure it is gone for good;
3. asks you to type the old controller's name;
4. stops the local Hyperlite service, keeps the node's previous database next to it
   (`hyperlite.db.before-promote-<date>`), installs the copy and the keys;
5. makes the copy this node's view: its own entry in the node list disappears (it is `local` now), and whatever
   pointed at the old controller's `local` now points at a node named after the old controller;
6. starts the service and prints the dashboard address.

Options: `--copy PATH` (another bundle, for example `previous.tar.gz`), `--old-controller HOST` (when the copy's host
name is not reachable by that name), `--self-name NAME` (this node's name in the cluster when it differs from its host
name), `--yes` (no question, for a script).

Users sign in on the new controller with the same accounts, passwords, 2FA codes and API tokens. Security keys are
bound to the host name they were added with: they work again only if the dashboard is reached through the same
name (a DNS change or a floating name), otherwise use the code and add the key again.

## When the old controller comes back

Do **not** start its Hyperlite service again. Register it as a node of the new controller, under the name the
promotion printed, so its VMs appear again and the HA records match.

## Limits

- Promotion is manual. Automatic failover needs fencing and a witness (see the [HA design](design/ha-automatic.md))
  and comes later, with a standby that receives changes continuously (option (b)).
- The dashboard address changes with the controller, unless you put a floating DNS name in front of both.
