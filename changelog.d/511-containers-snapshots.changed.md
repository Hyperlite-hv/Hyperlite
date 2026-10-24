- A container's snapshots are listed, taken, restored and deleted by `hyperlite-daemon` instead of the API (#511,
  step 3, containers part 3). Its console log (what `docker logs` shows) is read through the daemon too. The daemon
  finds the container's directory in its definition, so a snapshot is only ever written beside a filesystem Hyperlite
  manages. Every check runs before the task starts, so a refusal is the answer. The answers are unchanged.
