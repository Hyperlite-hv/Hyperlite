- A container is created and cloned by `hyperlite-daemon` instead of the API (#511, step 3, containers part 2). The
  daemon checks the request again: the name, the account, the network, the limits and the storage pool, read from the
  pool's own definition. It then pulls and unpacks the image, prepares the filesystem, reserves an application
  container's address and writes the definition, escaping the network and the directory. Unknown fields in a
  request are refused. A system container that does not start is no longer left defined without its filesystem.
  An application container whose process did not start said "Container defined purpose its process did not start":
  it now says "Container defined but its process did not start".
