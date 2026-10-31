- A task's log now records each privileged step it takes: the daemon operation and its target (a VM, a pool, a
  node...), how long it took, and how it ended, with the error when it failed. Secrets and arguments outside a short
  list of names are never written. A failed task can be troubleshot from its log alone.
