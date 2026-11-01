- The node's Network page creates, edits and deletes the host's bridges, bonds and VLAN interfaces, as Proxmox VE
  does. A change is shown as a diff first, applied interface by interface, and must be confirmed from the page within
  its delay (60 s by default): otherwise the node puts its previous configuration back by itself, so a change that
  cuts the administrator off undoes itself. Hyperlite writes its own file, `/etc/network/interfaces.d/hyperlite`, and
  never the administrator's `/etc/network/interfaces`. Bonds need the `ifenslave` package, now recommended.
