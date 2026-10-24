- The storage pools made, renamed and removed from the storage page on this node are handled by `hyperlite-daemon`
  instead of the API (#511, step 3, storage domain part 2a). This covers directory, NFS, SMB, iSCSI and LVM pools, with
  the SMB credentials file, the iSCSI CHAP secret and the LVM thin pool. It also covers the NFS and SMB permission
  check, and the `default` pool made at the first listing. The daemon writes a pool's definition itself from checked
  fields, checks them again, and refuses to remove a pool that holds files or that a VM uses. A pool on another node
  keeps that node's connection, with the same checks and answers. The answers are unchanged. A pool's refused creation,
  rename or removal is now always written into the audit log.
