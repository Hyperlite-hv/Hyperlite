- Each task records the process that runs it, so tasks are handled correctly once some run outside the API
  (#511, step 4c: `hyperlite-scheduler`, HA's services):
  - a restart of one service no longer closes as interrupted the tasks another one still runs;
  - cancelling such a task asks the process that runs it to stop, instead of closing its record while it goes on.

  The tests also reset libvirt's last error before each test, which made an HA test fail now and then.
