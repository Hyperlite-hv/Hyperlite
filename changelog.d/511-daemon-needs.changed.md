- `hyperlite-daemon` checks what each of its operations needs of the person who asks (#511, step 4d), as Proxmox VE's
  `pvedaemon` checks each method's permissions:
  - an administrator by default;
  - otherwise the privilege the route itself asks for, on the VM, container, storage pool, network or backup store
    it names;
  - or the account itself, for its own second factor, keys, tokens and password.

  A request whose route forgot a check is refused by the daemon (403). Calls of this node's own services keep working.
