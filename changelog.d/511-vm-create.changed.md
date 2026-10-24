- A VM is created by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 3a). The daemon checks the
  specification again against its own libraries, pools and VMs, then makes the disks, the cloud-init drive, the DHCP
  reservation and the definition. It writes the definition from the checked fields, never from XML or paths sent by
  the API. The answers are unchanged.
