- Hosts driven over SSH from one node (one controller, nodes over SSH), the configuration copy and `hyperlite-promote`
  are deprecated (#416): they work as before for as long as API v1 lasts and go with v2. A node joins a cluster of
  equal nodes instead (the **Cluster** page), as on Proxmox VE; a site across a WAN will be one of several autonomous
  sites driven from one view (#656). Their routes (`POST /nodes`, `POST /nodes/test`, `GET /nodes/cluster-pubkey`,
  `GET` and `POST /nodes/config-copy`) are marked `deprecated` in the API's description and answer with a
  `Deprecation` header and a `Link` to `docs/cluster-failover.md`; the dashboard says so where such a host is added.
