- The volumes created and deleted from the storage page, on this node's libvirt pools, are handled by
  `hyperlite-daemon` instead of the API (#511, step 3, storage domain part 2b). The daemon writes a volume's definition
  itself from its checked name and size, and refuses to delete a volume a VM uses, whatever the API checked. The
  answers are unchanged.
