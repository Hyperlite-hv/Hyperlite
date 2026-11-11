- A scheduled backup that fails is tried again within the hour, and logged, instead of being skipped until its next
  period (#663).
- A manual backup refused before its copy starts (VM gone, a disk it cannot copy, libvirt unreachable) is a failed task
  in the journal and the audit log, instead of a 202 with nothing behind it (#663).
- Deleting a VM of another node, or migrating one, says which disks could not be removed there instead of ignoring the
  ssh error (#663).
- A VM creation that fails halfway removes the disks and drives it made, so it can be tried again under the same name
  (#663).
- Two copies of one ISO image between remote nodes no longer share a relay file (#663).
