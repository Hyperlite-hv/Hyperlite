- Container snapshots (#424): a stopped container's files can be snapshotted, restored and deleted
  (`GET/POST /containers/{name}/snapshots`, `POST …/{snapshot}/restore`, `DELETE …/{snapshot}`), from its new
  Snapshots tab. libvirt's LXC driver has no snapshot: each one is an rsync copy of the container's directory, next to
  it, keeping only the files that changed since the previous one (compared by content). A restore copies the snapshot
  and swaps it in, and leaves the container's files as they were if it fails. Snapshots follow a renamed container and
  go with a deleted one; one operation at a time runs on a container's files. The package depends on `rsync`.
