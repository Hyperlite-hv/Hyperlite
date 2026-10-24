- The database health test suite passes on Debian 13 (#440): its SQLite (3.46) already sees, in the daily quick check,
  index entries that disagree with their table, which the test expected only the full check to see. Hyperlite then
  names the same index and offers the same online repair.
