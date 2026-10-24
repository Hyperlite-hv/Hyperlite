- The ISO library and the disks to import are written by `hyperlite-daemon` instead of the API (#511, step 3, disks
  domain part 1). It stores an upload, renames or deletes an ISO, deletes a disk to import and extracts an OVA's
  disks. An upload is written into a spool (`/var/lib/hyperlite/spool`), then moved into its library by the daemon,
  as Proxmox VE's pveproxy and pvedaemon do. The daemon checks the names again and refuses anything in the spool that
  is not a file of its own (a link to another file). It keeps an ISO that a VM has in its CD-ROM drive. The answers are
  unchanged. An interrupted upload left in the spool for more than a day is removed when the service starts.
