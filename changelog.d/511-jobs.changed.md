- The automation jobs' steps run in `hyperlite-daemon` instead of the API (#511, step 3): a command as root on the
  host, or over SSH with the automation key on a VM, whose address and account the daemon reads itself. Cancelling a
  run still stops the step in progress. A step's output is kept up to 1 MiB each, then marked as truncated.
