- Two backups of a VM into a store within the same second (an unchanged VM's incremental takes less) no longer
  collide: the second waits for the next second instead of finding the first's directory or checkpoint already
  named (#420).
