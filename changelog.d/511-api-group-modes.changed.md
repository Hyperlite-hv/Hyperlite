- The files the web API reads and writes are now shared with the `www-data` group, as on Proxmox VE (#511): the
  database, the claim files, the TLS certificate's key, the upload directories and the access log. A
  `tmpfiles.d` entry, `/etc/tmpfiles.d/hyperlite.conf`, sets this up at every boot. The API itself still runs as root;
  this prepares it to run as `www-data`.
