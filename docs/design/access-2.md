# Access 2: perimeters, role catalogue, protections and dated rights

Status: implemented in steps (one pull request per section group).

Hyperlite's first permission model (`app/core/permissions.py`) gives two global roles (administrator, observer) and
assignments of a role on one object (a VM, a pool of VMs, a container, a storage pool, a network, a backup store).
Everything else (backups, alerts, updates, nodes, the cluster, accounts) is an administrator's. That leaves a team two
choices: make someone an administrator of everything, or nothing. This document widens the model without changing what
existing accounts can do.

Principles kept from the first model:

- **Additive.** An assignment only grants. There is no deny; an administrator keeps every right everywhere and an
  observer keeps global read access.
- **The daemon decides.** `hyperlite-daemon` checks every privileged operation against its own copy of the
  permissions (`app/daemon/needs.py`); the API's checks only give earlier, clearer refusals.
- **Mixed versions.** A cluster updates one node at a time. Every new kind of row is refused until every member says
  it knows it (`app/core/statd.py`, `FEATURES`), and what older versions cannot store goes in new tables (an older
  node ignores a table it does not know), never in new columns of a shared table.

## 1. Perimeters

An assignment is given on a **perimeter**. Perimeters nest:

```
cluster (one, id "*")
├── node (by name)
│   ├── VM, container (by name)
│   └── storage pool, network, backup store (by name)
└── pool (by id) ── the VMs that are its members, on any node
```

A right held on a perimeter holds on everything under it:

| Checked on      | Perimeters that grant it                       |
|-----------------|------------------------------------------------|
| a VM            | the VM, each pool that contains it, its node, the cluster |
| a container     | the container, its node, the cluster           |
| a pool          | the pool, the cluster                          |
| storage, network, backup store | the object, its node, the cluster |
| a node          | the node, the cluster                          |
| the cluster     | the cluster                                    |

"Its node" is the node that answers: every route about a VM or a container runs on the node that hosts it (requests
for another member are forwarded, `app/core/cluster_view.py`), so the daemon there checks its own name. The cluster
perimeter is stored as `resource_type = 'cluster'`, `resource_id = '*'`; a node as `resource_type = 'node'` and its
name.

## 2. Area privileges and the role catalogue

The privilege catalogue gains **area privileges**, held on the cluster or a node:

| Area     | Privileges                         | What they open                                       |
|----------|------------------------------------|------------------------------------------------------|
| node     | `node.view`, `node.manage`         | host status and settings, maintenance, services     |
| storage  | `storage.view`, `storage.manage`   | storage pools, replication                           |
| network  | `network.view`, `network.manage`   | networks, bridges, firewall                          |
| backup   | `backup.view`, `backup.manage`     | backup jobs, groups, stores, restores                |
| alert    | `alert.view`, `alert.manage`       | alert rules, notification channels, reports, metric servers |
| audit    | `audit.view`                       | the journal                                          |
| update   | `update.view`, `update.apply`      | update status, history, preflight, installing        |
| cluster  | `cluster.view`, `cluster.manage`   | HA, load balancing, cluster settings                 |
| access   | `access.view`, `access.manage`     | assignments, groups and roles inside the perimeter   |

Some areas are the whole cluster's configuration (alert rules, backup jobs, HA, load balancing): their privileges
count only when held on the cluster perimeter. Node-bound areas (host settings, a node's networks and storage, its
update) also count when held on that node.

New predefined roles, next to the existing ones (Reader, Operator, Manager, Backup store reader):

| Key               | English          | French                   | Holds                                                      |
|-------------------|------------------|--------------------------|------------------------------------------------------------|
| `steward`         | Steward          | Responsable              | every privilege, including granting access in its perimeter |
| `builder`         | Builder          | Exploitant               | every privilege except `access.*`                         |
| `observer`        | Observer         | Observateur              | every `*.view`                                            |
| `access_delegate` | Access delegate  | Délégué aux accès        | `access.view`, `access.manage`                            |
| `backup_keeper`   | Backup keeper    | Gardien des sauvegardes  | `backup.*`, `vm.view`, `vm.snapshot`                       |
| `watcher`         | Watcher          | Veilleur                 | `alert.*`, `audit.view`, every `*.view`                    |
| `maintainer`      | Maintainer       | Mainteneur               | `update.*`, `node.view`, `node.manage`                    |

Granting: an account with `access.manage` on a perimeter may give or remove assignments on that perimeter and below
it, with any role except one holding `access.manage` itself unless it holds it on a wider perimeter (a delegate cannot
create other delegates beside it). Only an administrator assigns on the cluster perimeter or makes administrators.

Routes move from `require_role("admin")` to `require_area(privilege, reach)` area by area. A route whose work also
runs a daemon operation moves only with that operation's need (`app/daemon/needs.py`, `on_area`).

`GET /access/me` gives the signed-in account its effective privileges on this node and the cluster, so the dashboard
shows the actions it may take instead of guessing from the global role.

## 3. The Rights tab

Every VM, container, pool and node gets a **Rights** tab ("Droits"):

- **Who can do what**: the assignments on the object and those it inherits, each with the perimeter it comes from (a
  pool, the node, the cluster) and a link to it.
- **Test an account**: pick an account and see each privilege it holds on the object and the assignment that grants
  it (`GET /access/explain`).
- **Assign**: a short wizard (who, which role, until when) instead of the old three selects.

## 4. Protections

A **protection** on a VM, a container, a pool or a node (`resource_protections` table):

- **Keep**: the object cannot be deleted (VM, container, its disks and snapshots) until the protection is lifted.
- **Freeze**: no change at all (configuration, power, snapshots); only reading and the console.

A protection on a pool or a node holds for what is under it; on a pool itself, Keep refuses deleting the pool and
Freeze changing its members. The API refuses a protected change before any route runs (423, code `protected`), and
the automatic deletion of expired VMs waits until the protection is lifted. Lifting one is a separate, journaled action that needs
`access.manage` on the perimeter; administrators are not exempt (that is the point), they only may lift it.

## 5. Envelopes and guardrails

An **envelope** caps what a pool may hold (`resource_envelopes`): VMs, vCPUs, memory, disk. Creating, cloning or
resizing a VM in the pool, or adding one to it, past its envelope is refused with what would be exceeded. The VMs
are counted wherever they run; their vCPUs, memory and disk are those of the members the answering node hosts (a VM's
size is its node's to read).

**Guardrails** are creation rules held on the cluster or a pool (`guardrails`): storage pools and networks allowed,
the largest VM (vCPU, memory, disk), a name pattern. They are checked when a VM is created or
cloned; administrators are held by them too (an administrator can change them).

## 6. Dated rights and on-demand rights

An assignment may end: `access_terms` (one row per assignment that has terms) holds its end, its reason and who gave
it. An ended assignment grants nothing; the scheduler removes it and journals it.

**On-demand rights**: an account may ask for a role on a perimeter for a time (up to a maximum the administrator
sets), with a reason; with a second factor when it has one. The request is granted at once (self-service) or waits
for an approval from an administrator or a delegate, as the perimeter's setting says. A granted request becomes a
dated assignment; requests, approvals and ends are journaled and notified.

## 7. Delegated tokens

An API token may be **delegated**: it acts with only a subset of its owner's rights (a role on a perimeter), never
more than the owner holds when it is used. A pipeline gets a token that creates VMs in one pool and nothing else.

## 8. Tag restrictions and the access review

An assignment may carry a **tag restriction**: it grants only on the VMs and containers carrying that tag (`env:dev`).

The **access review** lists, for an administrator: every assignment with its age and its last use, accounts not
signed in for 90 days, assignments to deleted accounts or groups, dated assignments about to end, and delegates. Each
row can be kept (noted) or removed from the page.
