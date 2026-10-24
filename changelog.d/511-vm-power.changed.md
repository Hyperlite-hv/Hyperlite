- Starting, shutting down, stopping, rebooting and resetting a VM of this node are done by `hyperlite-daemon` instead
  of the API (#511, step 3, VM domain part 1). This covers the dashboard's buttons, the repeated shutdown request,
  the start at boot and libvirt's autostart flag. The VM's pre-stop hook script is run as root by the daemon, and the
  guest agents' addresses in the VM list are read through it. The daemon checks the VM's state again. A VM on a node
  registered over SSH only keeps that node's connection. The answers are unchanged.
