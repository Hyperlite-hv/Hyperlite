- The single sign-on and LDAP settings are written by `hyperlite-daemon`, as the other permissions since 4b (#511).
  These settings decide who may sign in and which of the identity provider's or directory's groups are
  administrators. The daemon also seals their client secret and bind password with the node's keys; the API did this
  until now.
