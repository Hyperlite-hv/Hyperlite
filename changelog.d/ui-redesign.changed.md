- Dashboard redesign for clusters of several nodes: a rail of sections beside an inventory tree (cluster, nodes and
  their VMs with their VMID, containers, library), a top bar with the running operations and the alerts, and a strip
  listing the operations while they run. The cluster's pages sit under its header as tabs; its summary shows the
  quorum, high availability, alerts, maintenance, the capacity used on each node with the room kept for a node failure,
  and the nodes. Cool neutral palette in both themes.
- Nodes: a fleet view filtered by state, with each node's processor load hour by hour over the last 24 hours.
- VM summary: where the VM runs, what happens if its node fails, its recent moves, and its disks and network as a table.
  **Migrate** is in the VM's header; the dialog ranks every other node by the room it would have left and says why a
  node cannot take the VM.
- Sign-in: one centred card.
