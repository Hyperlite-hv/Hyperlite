- The migration's compatibility checks, a node's compatibility and the drain plan are made by hyperlite-daemon,
  which reads the VMs' migratable definitions; the API reads libvirt only through its read-only socket.
