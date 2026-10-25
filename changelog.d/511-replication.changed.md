- The replication's points run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 1b): the daemon
  copies a VM into the other site's directory, full or incremental through libvirt's backup API, starts a stopped VM
  paused for the copy and prunes the old chains. It takes the chain's state from the API's records only as point and
  checkpoint names of its own form, never as paths. The site recovery reads the VMs through a read-only connection
  and restores through the daemon, as the backups do.
