- Updates: a download of the installed package that hangs (or fails on a node without network) no longer fails the
  whole update; an update from a file keeps the installed package from its own repository (#676).
- A second update can no longer start next to one that runs longer than 15 minutes: the guard follows the update's
  own unit (#676).
- A rolling update waits for each node to answer with the new version, instead of moving on while the old process
  still answers (#676).
