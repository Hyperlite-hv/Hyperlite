- The way back from the node's files in the cluster's tree (#525) trusts only what root made: a copy of
  `/etc/hyperlite` or a permissions database in `/var/lib/hyperlite`, a directory the API's account can write to, is
  used only when it is root's and nobody else can change it. SMB storage pools get their credentials' path back too.
