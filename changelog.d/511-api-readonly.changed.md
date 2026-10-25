- The API's last local libvirt changes outside HA go through `hyperlite-daemon` (#511, step 4 preparation): the
  dashboard's default storage pool, made when missing and read again, and the guests' shutdown before the host
  reboots or powers off. Every route now reads libvirt through a read-only connection.
