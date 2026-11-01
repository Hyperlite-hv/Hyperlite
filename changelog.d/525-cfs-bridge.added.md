- The way back from the node's files in the cluster's tree (#525, step 2): installed over a later version that keeps
  the node's certificate and keys in hyperlite-cfs's tree (a rollback), the package puts them back in `/etc/hyperlite`
  first, never over a file already there, before it looks for the node's keys. Nothing changes on a node today.
