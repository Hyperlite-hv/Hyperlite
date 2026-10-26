- The replicated configuration service (`hyperlite-cfs`) now accepts changes from root only (#511). Its socket is
  readable by the web API's group, `www-data`. The API's own few changes (a new row's id, the rolling update's lock, the
  certificate fingerprint, the node's lead) now go through `hyperlite-daemon`. Each refused change is written to
  hyperlite-cfs's log with the caller's uid.
