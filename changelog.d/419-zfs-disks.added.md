- ZFS pools on the node's disks (#419), as Proxmox VE's Disks › ZFS: on free whole disks (never one holding data or a
  VM's), as a single disk, a mirror or RAIDZ1/2/3; their health with every disk's errors, a scrub, a disk attached (a
  mirror more, a RAIDZ grown), room added, a failed disk replaced, from *ZFS health* in the pool's menu. A pool that
  degrades is notified (event "ZFS pool health"). A kernel updated without its headers, which leaves ZFS without its
  module, is now said as such.
