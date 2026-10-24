- The storage page's reads that need root on this node are done by `hyperlite-daemon` (#511, step 3, storage domain
  part 2c). That is a volume group's LVM report, which gives an LVM pool's thin pool and its thin volumes, and a pool's
  refresh before its volumes are listed. The storage domain of the storage routes is then done. The answers are
  unchanged.
