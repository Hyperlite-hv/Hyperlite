- libvirt's networks are created, started, stopped, edited, renamed and deleted by `hyperlite-daemon` instead of the
  API (#511, step 3, network domain part 2a): the daemon writes a new network's definition itself from checked fields
  (`app/core/network_create.py`), never libvirt XML from the API, and checks again that a system network or one a VM
  still uses is not deleted. The routes and their answers are unchanged; without the daemon they answer that it is
  unavailable (503). A list of the networks no longer redefines the isolated network every time: only when it is
  missing or stopped.
