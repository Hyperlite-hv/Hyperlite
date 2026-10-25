- The backup stores run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 2): a store made or
  taken over, an encrypted copy unlocked, a backup into a store, an import into one, a copy job, a garbage collection,
  what a store holds, and another cluster's stores. Only the daemon opens a store's key and a remote's token. A
  remote's token is now sealed with the address and the certificate it was given with, and is only ever sent there,
  whatever the records say later; a remote recorded before keeps the address of its record.
