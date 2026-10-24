- The copies and the commands between nodes are done by `hyperlite-daemon` instead of the API (#511, step 3, cluster
  domain part 4). This covers an ISO copied to or from another node, listed or deleted there; the configuration copy
  (the daemon builds the bundle with the node's keys and sends it); a node's boot id and host name; the fixed probes
  of another node's statistics and hardware; and the peer calls' fallback over SSH, which still carries the user's
  ticket. The API names a node and the work, never an address, a path or a script. Another node's summary, address
  use and metrics are read through the daemon's read-only tunnel. The answers are unchanged.
