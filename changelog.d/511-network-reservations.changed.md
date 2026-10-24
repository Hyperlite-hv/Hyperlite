- A network's DHCP reservations, added and removed from the network's page, are written by `hyperlite-daemon` (#511,
  step 3, network domain part 2b), which checks the MAC address, the name and the address again. The routes and their
  answers are unchanged.
