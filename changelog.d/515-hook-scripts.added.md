- Hook scripts on a VM's lifecycle, as Proxmox VE's hookscripts (#515): an administrator attaches a script that root
  placed in `/var/lib/hyperlite/snippets/` on the node (Hardware > Advanced > Hook script, `PUT /vms/{name}/hookscript`).
  It runs as root with the VM's name and the phase at pre-start, post-start, pre-stop and post-stop, for every start
  and stop (the dashboard, the start at boot, HA, `virsh`) through libvirt's qemu hook, pre-stop before a stop asked
  through Hyperlite. A failing pre-start refuses the start, a failing pre-stop refuses a shutdown (not a forced stop);
  the output is in the task's log and the VM's last runs.
