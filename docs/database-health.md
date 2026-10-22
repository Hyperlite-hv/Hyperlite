# A damaged database

Each node keeps its state in one SQLite file, `/var/lib/hyperlite/hyperlite.db`. A hard stop of the host (a power
cut, a VM killed while its disk caches writes) can damage it, most often the indexes of the tables written all the
time: tasks, their logs, the audit log, the metrics. SQLite only reports the damage to the statement that reads a
damaged page, so Hyperlite checks the file itself (`app/core/db_health.py`):

- at start, and once a day, with `PRAGMA quick_check` (every page is read);
- when a background loop (metrics, node poller, backup scheduler, configuration copy) fails 3 times in a row with a
  corruption error, with the full `PRAGMA integrity_check`, which also compares every index with its table;
- when an administrator asks for it: **Check again** on the banner, or `POST /api/v1/host/database/check`.

A damaged database shows a banner above every page of the node's dashboard, and sends a **Database check**
notification to the channels that subscribe to it (Notifications page). The banner says what is damaged and which
repair fits. `GET /api/v1/host/database` gives the same, for a script or a monitoring system.

A loop that keeps failing while both checks find nothing shows the banner too, with the loops' names: the service
log (`journalctl -u hyperlite`) has their errors.

## The three repairs

### Index entries that disagree with their tables: Repair now

The pages are sound, but an index misses rows or has too many (a disk that lost its last writes leaves this). An
administrator clicks **Repair now** on the banner (`POST /api/v1/host/database/repair`). Hyperlite rebuilds those
indexes with `REINDEX`, with the service running, checks the whole file again and writes the result in the audit
log.

### Damaged index pages: rebuild the file, with the service stopped

SQLite cannot free a damaged page: `REINDEX`, `DROP INDEX` and `VACUUM` all fail on it. The file is rebuilt from its
tables, read without their indexes, and the indexes are made again from the rows:

```bash
systemctl stop hyperlite
cd /usr/lib/hyperlite
venv/bin/python -m app.core.db_health check --full      # what is damaged
venv/bin/python -m app.core.db_health rebuild
systemctl start hyperlite
```

The rebuild refuses to run while the service runs. It checks the new file before putting it in place, and keeps the
old one next to it as `hyperlite.db.damaged-<date>` (with its `-wal` and `-shm` files), so nothing is lost if the
result is not what you expected. If a table cannot be read, nothing changes.

### Damaged tables

**A history table** (`audit_log`, `tasks`, `task_logs`, `job_run_logs`, `metrics_samples`, `storage_samples`,
`node_live`): the rebuild can leave it empty, its history lost, and keep everything else:

```bash
venv/bin/python -m app.core.db_health rebuild --drop-history
```

**A configuration table** (users, permissions, nodes, HA, backup jobs...): its rows cannot be trusted, and Hyperlite
has no command to restore them yet. Before changing anything:

- keep a copy of the whole directory (`hyperlite.db`, `hyperlite.db-wal`, `hyperlite.db-shm`), service stopped;
- for nodes driven over SSH from a controller, every node holds a copy of the controller's configuration from the last
  15 minutes: see [Taking over when the controller is lost](cluster-failover.md);
- in a cluster, the other members still hold the replicated configuration. **Do not use "Copy the database again"
  on this node**: it copies this node's database to the whole cluster, the damage included. Ask on the project's
  Coordination issue before acting.
