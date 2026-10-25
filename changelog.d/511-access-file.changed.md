- The accounts, API tokens, groups, roles, resource pools, ACLs and SSO/LDAP settings move to a file of their own,
  `/etc/hyperlite/access.db`, readable and writable by root only, at the first start of this version (#511). A table of
  those names made in the main database is never read in its place. A return to an older version puts them back into
  the database.
