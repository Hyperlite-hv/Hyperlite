- Cluster membership: the reverse SSH trust of a node is matched by its whole name, so registering `node1` after
  `node10` gives it its line, and removing `node1` no longer removes `node10`'s (#675).
- A join that fails no longer deletes a node registered over SSH under the joiner's name before it (#675).
