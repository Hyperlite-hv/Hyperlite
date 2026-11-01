- A VM's or a network's firewall references security groups of the nodes' firewall (Datacenter › Firewall): their rules
  apply to the incoming traffic before its own, and a change to a group applies at once wherever it is used. A group a
  VM still uses is kept until no VM references it.
