- The consoles and the shells are opened by `hyperlite-daemon` instead of the API (#511, step 3, consoles domain):
  the host shell, a container's root shell and SSH terminal, and a VM's serial console, SSH terminal, VNC console and
  SPICE display. The daemon runs the session as root and hands its terminal or its socket to the API, which relays it
  to the browser. It reads the display's port, the guest's address and account itself, and reaches another node with
  the host key recorded when the node was registered. The host shell starts in a clean environment instead of the
  service's. When the SSH terminal cannot connect, ssh's own message is shown, then Hyperlite's.
