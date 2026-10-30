# SPICE console (#518)

Proxmox VE offers, besides noVNC, a SPICE console opened by virt-viewer (`remote-viewer`) from a `.vv` file: better
display, audio, USB redirection, several screens. Wanted here: a VM can have a SPICE display; the dashboard hands out a
short-lived `.vv` file; no SPICE port is ever open on the network.

## What was checked on a real host

libvirt 10.0, QEMU 8.2 (TCG), `remote-viewer` 11.0 (spice-gtk, GLib 2.80) under Xvfb, a VM with both a VNC and a
SPICE display.

| What | Result |
|---|---|
| QEMU without `qemu-system-modules-spice` | No SPICE at all ("There is no option group 'spice'"). The package is only *recommended* by `qemu-system-x86` (Ubuntu 24.04; Debian's archive was not reachable from the lab), so Hyperlite must depend on it itself |
| VNC and SPICE displays on one VM | Both start (5900 and 5901 on 127.0.0.1): noVNC keeps working |
| `remote-viewer` with `proxy=http://…` in the `.vv` | Sends `CONNECT <host>:<port> HTTP/1.0`, the `.vv`'s `host` value as is: a ticket can travel there. It opens **one connection per SPICE channel** (4 to 5 at start), so a ticket must admit several connections while it is valid |
| uvicorn (Hyperlite's HTTPS server) as that proxy | Not possible: httptools answers `400 Invalid HTTP request`; h11 hands `CONNECT` to the application, but ASGI cannot take over the socket. The proxy is a listener of its own, as Proxmox VE's `spiceproxy` (port 3128) |
| An `https://` proxy with Hyperlite's self-signed certificate | Refused by the client ("Unacceptable TLS certificate"); a `.vv` cannot carry a CA for the proxy. Hence a plain HTTP proxy, with SPICE itself in TLS |
| SPICE in TLS (`spice_tls = 1` in `qemu.conf`, `defaultMode='secure'`) | QEMU listens on the TLS port only, no plain port. Through the proxy, with the CA and the server's subject in the `.vv` (`ca=`, `host-subject=`): the console opens |
| A `.vv` with another CA / another subject | Refused by the client: "server certificate not being signed by the provided CA" / "subjects mismatch" |
| A wrong password | TLS passes, SPICE refuses: "main channel: auth failure" |
| A password set on the running VM (`updateDeviceFlags`, `passwd` + `passwdValidTo` 20 s ahead, `connected='keep'`) | Connects within the 20 s; the old password no longer does; **the open session survives the expiry**; a new connection after it is refused; the stored configuration keeps its own password |
| A stored password already expired (`passwdValidTo` in 1970) | The VM starts; nobody connects, not even with an empty password, until a ticket sets a new one |

| libvirt 10 and a drop-in file (`/etc/libvirt/qemu.conf.d/`) | Not read: `spice_tls` must be in `qemu.conf` itself |
| A VM with a TLS-only SPICE display on a node without `spice_tls` | Does not start: "Auto allocation of spice TLS port requested but spice TLS is disabled in qemu.conf" |
| A configuration rewritten from libvirt's ordinary XML (as most of Hyperlite's settings changes do) | The display's password and its expiry are gone: they are only in the secure XML. Started again, QEMU **admits a client with no password** on the TLS port |
| The same display, with a random `spice_password` in `qemu.conf` | Refused without a password ("auth failure"); a ticket still opens it (the live password wins). The password never shows on QEMU's command line |

A SPICE display **without** a password admits anyone who reaches its port. Two locks: every SPICE display Hyperlite
defines carries a random, already expired password; and each node's `qemu.conf` (root-only) has a random default
`spice_password` nobody is told, for a display that lost its own.

### Through Hyperlite (the real backend on that host)

| What | Result |
|---|---|
| Ticket for a VM without a SPICE display | 409, "add one" |
| SPICE display added to a running VM | Takes effect at its next start (409 for a ticket until then); afterwards VNC and a TLS-only SPICE display, no plain port |
| The `.vv` from the API, and the one the dashboard downloads (French and English) | `remote-viewer` opens the VM through Hyperlite's proxy on 3128, in TLS; it deletes the file once read |
| The session after 30 s | Still open |
| A `.vv` opened 30 s after it was handed out | Refused by the proxy ("HTTP proxy connection not allowed"), logged |
| A made-up ticket | 403, logged without the request line |
| A VM defined as Hyperlite defines one (no `<video>`: libvirt chose `cirrus`), given a SPICE display through the API | Its video model unchanged; `remote-viewer` shows its screen |
| noVNC on the same VMs | Unchanged, connected |
| `scripts/ensure-spice-tls.sh` under a real systemd (an Ubuntu 24.04 virtual machine of the lab, libvirtd running) | libvirtd restarted through systemd (its process changed); run again, no restart |
| `scripts/ensure-spice-tls.sh` from a clean node | Certificates, `spice_tls`, `spice_password`; a running VM kept running across libvirtd's restart; run again, nothing changes |

## Design

- **Display**: a VM gets a SPICE display next to its VNC one (`<graphics type='spice' autoport='yes'
  defaultMode='secure'>` on 127.0.0.1, random expired password), effective at its next start, as the serial port is
  added today. Its video model is left as it is. VNC stays: noVNC is unchanged.
- **Node setup** (`scripts/ensure-spice-tls.sh`, run by the package's `postinst`): each node has its own SPICE CA
  and server certificate in `/etc/pki/libvirt-spice` (the directory Debian's AppArmor profile lets QEMU read; the
  CA's key is deleted once used), and `spice_tls = 1`, `spice_tls_x509_cert_dir` and a random `spice_password` in
  `qemu.conf` (libvirtd restarted once; running VMs are not touched, the setting applies to VMs started
  afterwards). An administrator's own settings are kept. The package depends on `qemu-system-modules-spice`. Every
  node needs it: a VM with a SPICE display does not start on a node without it, so a display is only added where
  the node is ready.
- **Ticket** (`POST /vms/{name}/spice-ticket`, the `vm.console` privilege as the VNC console): sets a random password
  on the running VM, valid 30 s, and records a proxy ticket for the VM, its node and its TLS port, valid as long.
  Answers the `.vv` content (`type=spice`, `host=<ticket>`, `tls-port`, `password`, `proxy=http://<the address the
  browser used>:3128`, `ca`, `host-subject`, `delete-this-file=1`, `secure-channels=all`).
- **Proxy** (a listener on 3128 inside the service): accepts `CONNECT <ticket>:<port>` only for a valid ticket and its
  own port, and relays to the VM's TLS port on 127.0.0.1 of its node, which hyperlite-daemon connects to (`ssh -W`
  with the cluster's key for another node) and hands over, as for the noVNC console (`console.display`). Anything else gets 403 and is logged. The proxy sees TLS only.
- **Dashboard**: the VM console's SPICE menu: "Open in virt-viewer" downloads the `.vv` (never shown), "Add a SPICE
  display" adds one for the VM's next start. Their outcome is said in the console itself: the console's own window
  has no toasts or dialogs.
- **Not added**: audio, USB redirection and several screens. The display alone gives what the VNC console gives
  (screen, keyboard, mouse), so a ticket takes the same `vm.console` privilege.

## Not checked yet

- `qemu-system-modules-spice` on Debian 13: checked on Ubuntu 24.04 only (Debian's archive was not reachable from the
  lab); the package's CI job resolves every dependency on Debian 13.
- AppArmor on Debian (QEMU reading `/etc/pki/libvirt-spice`; the lab has no AppArmor).
- A VM on another node (the relay through SSH): needs two hosts.
- Live migration of a VM with a SPICE display between nodes with different SPICE certificates.
- Windows and macOS `remote-viewer` builds.
