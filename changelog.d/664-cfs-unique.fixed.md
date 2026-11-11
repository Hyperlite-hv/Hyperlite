- Replicated configuration: a change from another node that breaks a rule of this node's database (two nodes giving
  the same name to two objects at once) is left out and named, and everything else is still applied. It used to block
  every later change from every node until the database was copied again (#664).
- Renaming a VM onto the name of a leftover record (a backup job) deletes that record in the whole cluster: the
  deletion was not sent, and the next apply failed on every node (#664).
