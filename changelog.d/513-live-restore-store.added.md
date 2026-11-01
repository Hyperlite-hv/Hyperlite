- Live restore from a backup store (#513): a backup in a store, encrypted or not, restores as a new VM that starts at
  once, as one kept as files does. The node serves the backup to the VM as a read-only disk while its disks are copied.
