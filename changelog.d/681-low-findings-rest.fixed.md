- Replicated configuration: a tree read just before a change of this node went out is no longer applied over it (#681).
- The one-time rename of the old `kvm-lab` label runs once, no longer at every start, where it took over the rows of
  a node really named so (#681).
- Rebuilding an empty table during the schema migration keeps its id counter, so ids given out before are never given
  again (#681).
- A VLAN-aware trial left unconfirmed by a restart of hyperlite-daemon is undone at its next start (#681).
- A NIC that is a port of a bridge or a bond is no longer offered as the base of a macvtap network (#681).
- A Kubernetes cluster or an ISO copy that fails before it starts no longer stays "already running" until the service
  restarts (#681).
- A migration whose address reservation fails on one network takes back the ones it made on the others (#681).
- Moving a VM's snapshot records for a migration puts back the ones already taken off when one refuses (#681).
