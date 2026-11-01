- Every VM and container has a VMID, a number from 100 unique in the cluster, as on Proxmox VE: given at once to
  existing guests and to every new one, kept through migrations, renames and restores, shown in the VM list, the VM's
  summary and the containers' list, and taken by the API wherever a name is (`/vms/104/start`). Names keep working.
