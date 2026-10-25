- Reading a VM (`GET /vms/{name}`) on the member a live migration is leaving answers 404 instead of a 500 when the VM is
  undefined there between its lookup and its read (#334).
