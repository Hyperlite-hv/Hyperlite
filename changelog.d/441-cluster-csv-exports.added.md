- In a cluster, the CSV exports of the tasks and of the audit log hold every node's rows, as the lists do (#441): each
  member's export is read while it arrives and merged newest first, with a last column `member` (`?cluster=true` on
  `GET /tasks/export.csv` and `GET /audit/export.csv`, which the dashboard now asks for). The members that did not
  answer are named in the `Hyperlite-Unreachable` header and said by the dashboard; a member that stops midway ends
  the download with an error instead of a file missing its rows.
