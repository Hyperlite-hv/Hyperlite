- The copy of the configuration to and from the cluster's replicated store (hyperlite-cfs) is done by `hyperlite-daemon`
  instead of the API (#511). This covers both loops, the confirmation of a change before its answer, and the actions of
  Administration > Replicated configuration. The daemon now loads the node's `/etc/hyperlite/priv/env`.
