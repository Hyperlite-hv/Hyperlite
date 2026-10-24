- Growing a VM's disk and moving it to another storage pool are done by `hyperlite-daemon` instead of the API (#511,
  step 3, disks domain part 2). The daemon is asked for the VM by its name and the disk by its target, never a path.
  It checks the request again, drops the replication checkpoints only once the request is accepted, and writes a
  move's progress into its task. Cancelling the task stops the copy in the daemon. The answers are unchanged.
