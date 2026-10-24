- Starting, stopping, renaming and deleting a container, and changing its settings, are done by `hyperlite-daemon`
  instead of the API (#511, step 3, containers part 1). The daemon finds a container's filesystem in its libvirt
  definition and only accepts it in a directory where Hyperlite puts containers. It no longer takes it from the API's
  records. A container whose definition names any other directory keeps that directory when it is deleted, and the
  audit log says so; its rename and DNS changes are refused. The answers are otherwise unchanged.
