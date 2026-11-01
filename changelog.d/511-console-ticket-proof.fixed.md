- With the API as `www-data` (1.6.0), the consoles opened again: the host's shell, a VM's display, terminal and serial
  console, a container's terminal and shell, and SPICE through virt-viewer. Their websocket brings a one-time ticket
  only, so the call to hyperlite-daemon carried no proof and was refused; the ticket now keeps the proof of the request
  that made it.
- With the API as `www-data`, a request no longer fails now and then with "unable to open database file": the API
  keeps one connection to the database open, so its `-wal` and `-shm` files are no longer made again by root's
  services, root's alone for an instant, at every last close.
