- LVM storage pools (#417): a pool on an existing volume group, or on an empty disk Hyperlite builds one on. A VM
  created there gets each disk as a logical volume (a raw block device, the cloud image or imported disk written into
  it), grown with `lvextend` and deleted with the VM. The pool's volume list shows the thin volumes and thin pools
  libvirt does not see, and the pool is removed only once its group holds no volume at all: libvirt's own pool
  deletion, which wipes the whole group, is never used. VM snapshots and live migration are refused on LVM for now.
