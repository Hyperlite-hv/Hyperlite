- Encrypted backup stores (#420, step 3; `docs/design/backup-store.md`): a store's chunks and the files of its backups
  (NVRAM, TPM state, configuration) are encrypted on the node with AES-256-GCM before they reach the pool, as Proxmox
  Backup Server's client encryption. The key is kept encrypted in the cluster's secrets and shown once, as a code, a QR
  code and a file; an optional master recovery key (made by Hyperlite and given once, or the administrator's own RSA
  public key) lets a backup be read again even when every node and secret is lost. An encrypted store is taken over
  with its key's code, and `python -m app.core.backup_store_recover` reads a backup with nothing but the store and one
  key (`docs/backup-store-recovery.md`).
