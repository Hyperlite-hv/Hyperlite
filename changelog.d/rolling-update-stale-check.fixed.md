- A rolling update no longer stops on a node whose update went well, saying "the new version did not start and was
  rolled back". The node's update task ends a moment before its service restarts, and the old process, still
  answering beside the new package, read as a rollback. A rollback now has to last two minutes to stop the run.
