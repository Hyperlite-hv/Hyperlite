- **An update started from 1.4.0 or 1.4.1 left the node half updated**: the update job ran inside hyperlite-daemon,
  which the package restarts, so the job died with it, apt lost its output and the API kept running the previous
  version against the new daemon and dashboard, until it was restarted by hand. The job runs in a unit of its own
  again. The update to this version is still started by the previous one: restart the API once afterwards
  (`systemctl restart hyperlite`) if it still reports the previous version.
