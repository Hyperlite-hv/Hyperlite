- A failure of `hyperlite-daemon` answers 500 (`internal`) again, as these routes did before their work moved to the
  daemon, instead of 502 (`peer_error`), which API v1 keeps for another member of the cluster: the host's settings
  (DNS, host name, time, syslog, automatic updates), the update channel, the start of an update or a rollback, and the
  VLAN-aware trial of a network (#511). A call the daemon cannot answer is always a 503 (`unavailable`) with the reason,
  and a network removed while a request was being handled is a 404.
