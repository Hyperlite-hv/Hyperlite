# Design: numeric guest identifiers, VMID (#517)

Status: **done**.

The reference platform numbers every guest (VMID, from 100, unique in the cluster), and its API, storage and scripts use it.
Hyperlite named guests by node and name only. Now every VM and container also has a VMID:

- **Handed out once in the cluster**: a counter in hyperlite-cfs (`/cluster/vmid-next`), moved on with a
  compare-and-set (`put` with the version read), tried again when another node moved it first. hyperlite-cfs runs on
  a single node too. A number is never handed out twice, not even after its guest is deleted.
- **Kept in the guest's own definition** (`<metadata>`, namespace `urn:hyperlite:vmid:1`), with the UUID it was given
  to: it follows the guest through a migration, a rename and a restore in place. A copy of a definition (a clone, a
  template's deployment, an import) has a new UUID, so it gets a number of its own instead of sharing one.
- **Given by hyperlite-daemon**: at its start (the guests made before this version) and every 30 s, to any guest
  without one, whatever made it (the API, virsh, an import). `app/core/vmid.py`.
- **Read with the definition** the lists already parse: `vmid` in `GET /vms`, `GET /vms/{name}`, the containers'
  list, and the cluster's status (so every member knows the others' numbers).
- **Taken wherever a name is**: `/vms/{vmid}/...` and `/containers/{vmid}/...` go on as `/vms/{name}/...`, to the
  member the VM runs on (`?node=` added) when it is another one. A name made of digits that a guest of this node has
  wins over a number. Names stay identifiers: nothing that used them changes (API v1 additions only).
- **Shown** in the VM list (before the name, and searchable), on the VM's summary and in the containers' list.

Not done: disk and storage names keep the VM's name; renaming them would change every
existing disk path.
