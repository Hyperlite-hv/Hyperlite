- The cluster's SSH keys are used by `hyperlite-daemon` instead of the API (#511, step 3, cluster domain part 1).
  This covers testing a node before it is registered, the public key shown to authorize it, and recording its host
  key. It also covers giving and revoking the reverse trust in root's `authorized_keys`, and the node checks of the
  node poller. The daemon reads a registered node's address from the node registry itself. It checks an address
  before using the key. The answers are unchanged.
