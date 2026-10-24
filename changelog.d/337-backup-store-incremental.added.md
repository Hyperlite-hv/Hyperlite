- Incremental backups into a backup store (#337, step 2 of `docs/design/backup-store.md`): a running VM's qcow2
  disks keep a dirty bitmap from one backup to the next, and only the 4 MiB chunks with a changed block are read
  again (measured on a 200 MB disk: 50 chunks read for the first backup, 3 after 10 MiB written, 0 with no change).
  Each backup stays a complete image. Raw files and block devices are read whole, their unchanged chunks still stored
  once; a whole read at least every 7 days; a snapshot, a move or a restore drops the bitmap, and the next backup
  reads the disk whole.
