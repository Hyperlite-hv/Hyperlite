- The parity page counts the replicated configuration as the only source of truth as done (#416): every node applies
  the cluster's configuration in one order, read-only without quorum, and shows every node's tasks, audit log,
  backups and their CSV exports; the hosts driven over SSH that still need a controller are deprecated.
