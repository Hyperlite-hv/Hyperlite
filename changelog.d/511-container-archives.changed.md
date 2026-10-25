- A container's backup archives run in `hyperlite-daemon` instead of the API (#511, step 3, backups part 4): the
  archive of a stopped container's filesystem, its deletion and its restore. The daemon archives the filesystem the
  container's definition names, so a container kept in a storage pool's directory is archived too (it used to be
  looked for in the default directory only), and it deletes or unpacks an archive of the containers' backup
  directory only.
