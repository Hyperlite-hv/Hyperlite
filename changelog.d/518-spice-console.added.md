- SPICE consoles (#518), as Proxmox VE's: a VM can get a SPICE display next to its VNC one (`POST /vms/{name}/spice-display`,
  effective at its next start), and the console's SPICE menu downloads a `.vv` file for virt-viewer
  (`POST /vms/{name}/spice-ticket`). virt-viewer goes through Hyperlite's SPICE proxy on port 3128, which admits only
  that ticket, for 30 seconds; SPICE is in TLS only, checked against the node's own CA, and listens on 127.0.0.1. The
  package sets each node up (`scripts/ensure-spice-tls.sh`: certificates, `spice_tls` and a default SPICE password in
  `qemu.conf`) and depends on `qemu-system-modules-spice`. Checked with `remote-viewer` on libvirt 10.0 and QEMU 8.2.
