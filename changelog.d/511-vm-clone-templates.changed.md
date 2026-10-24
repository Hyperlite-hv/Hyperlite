- A VM is cloned, and a template made, deployed, renamed and deleted, by `hyperlite-daemon` instead of the API (#511,
  step 3, VM domain part 3c). The template files are root's. The daemon checks the source VM's state, the names, the
  room for the copy and a template's linked clones again, and writes the new definition itself. A Kubernetes cluster's
  VMs are started and stopped through it too. The answers are unchanged. An unexpected failure during a clone is no
  longer described in the answer; the daemon's log has it.
