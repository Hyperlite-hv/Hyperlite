- Automation: a job step that times out or is cancelled stops what it started in the background too; a step like
  `long_task & sleep 600` kept the run open for ever (#669).
- A node drain that fails while it is being planned (a peer briefly unreachable) releases the node: every later drain,
  maintenance or rename of it was refused as "already being drained" until the service restarted (#669).
