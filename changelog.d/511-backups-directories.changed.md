- The directory backups and the VM exports run in `hyperlite-daemon` instead of the API (#511, step 3, backups
  part 1a): a backup's copy, its restore over the VM or as a new VM, its verification and its deletion, an export's
  copy and its deletion. The daemon checks a backup's directory itself (under the default directory or an
  administrator's target, never a system directory) and deletes in it only a backup's own files: a backup directory
  that holds other files is kept, and its deletion refused with the names of those files. Cancelling a backup's task
  still stops its copy.
