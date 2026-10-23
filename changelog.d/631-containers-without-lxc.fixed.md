- A node without libvirt's LXC driver lists no containers instead of answering 500, and a route on a named container
  answers 501, saying the node has no LXC driver (#631). A node installed from the package always has the driver.
