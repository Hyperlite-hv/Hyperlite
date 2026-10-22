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

A SPICE display **without** a password admits anyone who reaches its port: every SPICE display Hyperlite defines
carries a random, already expired password.

## Design

- **Display**: a VM gets a SPICE display next to its VNC one (`<graphics type='spice' autoport='yes'
  defaultMode='secure'>` on 127.0.0.1, random expired password, `qxl` video), on a stopped VM, as the serial port is
  added today. VNC stays: noVNC is unchanged.
- **Node setup**: each node has its own SPICE CA and server certificate in a root-only directory, and
  `spice_tls = 1` with `spice_tls_x509_cert_dir` in `qemu.conf` (libvirtd restarted once; running VMs are not
  touched, the setting applies to VMs started afterwards). The package depends on `qemu-system-modules-spice`.
- **Ticket** (`POST /vms/{name}/spice-ticket`, the `vm.console` privilege as the VNC console): sets a random password
  on the running VM, valid 30 s, and records a proxy ticket for the VM, its node and its TLS port, valid as long.
  Answers the `.vv` content (`type=spice`, `host=<ticket>`, `tls-port`, `password`, `proxy=http://<the address the
  browser used>:3128`, `ca`, `host-subject`, `delete-this-file=1`, `secure-channels=all`).
- **Proxy** (a listener on 3128 inside the service): accepts `CONNECT <ticket>:<port>` only for a valid ticket and its
  own port, and relays to the VM's TLS port on 127.0.0.1, or on its node through the cluster's SSH connection, as the
  noVNC console does. Anything else gets 403 and is logged. The proxy sees TLS only.
- **Dashboard**: the VM console page offers "SPICE (virt-viewer)" when the VM has a SPICE display, and "Add a SPICE
  display" on a stopped VM; the `.vv` is downloaded, never shown.

## Not checked yet

- Audio, USB redirection and several screens through the proxy (the channels exist; not exercised in the lab).
- A VM on another node (the relay through SSH): needs two hosts.
- Live migration of a VM with a SPICE display between nodes with different SPICE certificates.
- Windows and macOS `remote-viewer` builds.
