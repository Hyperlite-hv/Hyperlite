- A node that gives up joining a cluster (Corosync or hyperlite-cfs did not come up there) asks the member to take it
  out again, with a secret the member gave it when it let it in. The member kept listing it and counting its vote: a
  two-node cluster stayed without quorum until an administrator removed it by hand. A member before this version still
  says how to remove it (#608).
