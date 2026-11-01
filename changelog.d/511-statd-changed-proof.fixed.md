- With the API as `www-data`, a change made through Hyperlite reaches the cluster's status at once again, instead of
  at the next period (10 s): the change seen before the request is signed in no longer asks hyperlite-daemon without
  a proof, which it refused.
