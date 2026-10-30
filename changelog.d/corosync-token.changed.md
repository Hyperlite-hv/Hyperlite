- Corosync's token timeout is 10 s instead of 3 s once every member of the cluster runs this version: a member
  reached over the internet or a VPN no longer drops out of the ring at every latency spike, which made the cluster
  refuse every change for a few seconds. A member that is really gone is noticed in about 10 s. The cluster keeps
  Corosync's default while a member of an older version is in it.
