- Lockdown of `hyperlite-admin` (#516), as ESXi's lockdown mode: an administrator can refuse root of a node its
  session as `root@<node>` (`PUT /api/v1/admin-cli {"lockdown": true}`), on every member and for the sessions already
  open; root must then use a named account's API token (`--token-file`), so every action is under a person's name.
  Off by default.
