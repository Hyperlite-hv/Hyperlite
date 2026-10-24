- A VM's firewall, hook script, name and cloud-init drive are changed by `hyperlite-daemon` instead of the API (#511,
  step 3, VM domain part 2c). The daemon builds the firewall's filter from checked rules and checks a hook script again
  in the root library. It renames the files named after the VM with it, and writes the cloud-init drive private to
  root. The answers are unchanged. An interface the firewall could not be applied to said "Filter created purpose not
  applied": it now says "Filter created but not applied".
