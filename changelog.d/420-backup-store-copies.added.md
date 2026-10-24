- Copies of a backup store, as Proxmox Backup Server's sync jobs (#420, step 4, part 1): a store made as the copy of
  another, on a pool at the other site, receives on its own schedule the backups it lacks, only the chunks it lacks,
  copied as they are (encrypted ones never decrypted); the copy keeps a retention of its own, so a backup deleted at
  the source, by its retention, by mistake or by an attacker, stays in the copy. The recovery of a lost site now also
  reads a backup store (`/backup-store-syncs`; the dashboard comes next).
