- Linked clones (#423): a template can be deployed as a linked clone, whose disk is a qcow2 image backed by the
  template's disk; it is created at once and stores only its own changes (`linked` on
  `POST /templates/{name}/deploy`, off by default). A template is not deleted or renamed while a VM reads its disk,
  the template list names each template's linked clones (`linked_clones`) and whether it can have some (`linkable`),
  and a linked clone made into a template gets a disk of its own. Hot backups, disk moves, disk growth and internal
  snapshots of a linked clone were checked on libvirt 10.0 and QEMU 8.2.
