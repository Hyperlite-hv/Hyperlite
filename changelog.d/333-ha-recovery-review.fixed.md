- Automatic HA recovery (#333) is safer:
  - a node whose Hyperlite service was stopped normally (an update, a restart) is never powered off, and its HA VMs
    are never started elsewhere while they still run there;
  - a target node that cannot be reached is passed over for the next one, instead of stopping the round;
  - a VM an earlier round started is found where it runs and never started a second time;
  - each VM restarted takes its memory out of its node's free memory, so one node is not overcommitted;
  - a BMC that did not confirm the power-off is asked again, up to three times, before the VMs go to manual;
  - a leader that loses the lead mid-round restarts nothing more.
