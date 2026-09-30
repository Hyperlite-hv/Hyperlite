"""Storage behind the control plane services (docs/design/control-plane-v2-migration.md, section 4).

Only repositories know a storage backend. Routers call services; services call repositories through the Protocols
of interfaces.py. Today's backend is SQLite (sqlite/); etcd, YAML files and PostgreSQL come in later phases behind
the same Protocols.
"""
