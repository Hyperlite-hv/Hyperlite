- The ZFS pools and volumes made and removed from the storage page are handled by `hyperlite-daemon` instead of the API
  (#511, step 3, storage domain part 1): loading the kernel module, creating and deleting a pool, creating and deleting
  a volume. The daemon checks the names again and refuses to delete a volume a VM still uses. The routes and their
  answers are unchanged; the volume's deletion records "Confirmation missing" in English in the audit log.
