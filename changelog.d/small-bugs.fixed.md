- Dashboard: a network error or a 502/503 of `/auth/me` while the page loads (the service restarting) no longer signs
  the user out in every tab; only a 401 ends the session (#668).
- Web terminal: a large paste reaches the program whole instead of being cut, or ending the session (#667).
- Creating a role or a resource pool reports "already exists" only when the name is taken; another failure is its own
  error (#673).
- `hyperlite ssh`: the remote command keeps its own options (`-y`, `--server`, `--`), read no more by the command
  itself (#672).
- Installer: a failed `postinstall.sh` is an error the installer shows, instead of a reboot into a Debian without
  Hyperlite (#671).
- Publishing: a test publication no longer cancels a pending stable one (#670).
- A connection reset by hyperlite-daemon (restarted during a call) answers 503, not a raw 500 (#677).
- Cloud-init: a user or host name with a trailing newline is refused; an ISO name with `..` answers 422, not 500
  (#678).
