- The file restore runs in `hyperlite-daemon` instead of the API (#511, step 3, backups part 3): the daemon opens a
  backup's images with libguestfs, read-only, only in a backup's own directory, and hands a downloaded file to the
  API as an open file, removed as soon as it is sent.
