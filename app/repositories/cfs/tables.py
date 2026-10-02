"""The SQLite tables hyperlite-cfs holds (docs/design/hyperlite-cfs.md, phases C and D): configuration only.

Each entry names the columns left out because they change all the time and say nothing about the configuration (a
last run, a last check, a counter), whether the table holds secrets (copied under /priv, which the daemon serves to
root only), and rows that stay local. Tables not listed are state or history (audit log, tasks, metrics, backup
records, sessions, challenges): each node keeps its own.

A left-out column must be nullable or have a default, or be given a value in "fill" ("now": the current time), since a
row that comes from another node is inserted without it (tests/test_cfs_shadow.py checks this).

A column added to a listed table is copied without changing this file; a new table is copied only once listed here.
"""

TABLES = {
    # Accounts and access
    "users": {"priv": True, "volatile": {"last_login_at", "totp_last_step"}},
    "webauthn_credentials": {"priv": True, "volatile": {"sign_count", "utilise_le"}},
    "api_tokens": {"priv": True, "volatile": {"last_used_at"}},
    "groups": {},
    "group_members": {},
    "custom_roles": {},
    "acl": {},
    "pools": {},
    "pool_members": {},
    "sso_config": {"priv": True},
    "ldap_config": {"priv": True},
    # Nodes and the cluster
    "nodes": {"volatile": {"statut", "derniere_verification"}},
    "node_maintenance": {},
    "node_fencing": {"priv": True},
    "ha_settings": {},
    "ha_protected_vms": {"volatile": {"last_synced_at", "etat_ha", "derniere_action", "derniere_action_le"}},
    # The switch of this very mirror, and the cfs version this node last applied, stay per node.
    "app_settings": {"local_rows": {"cle": ("cfs_shadow", "cfs_applied_version")}},
    # Guests
    "object_meta": {},
    "vm_boot": {},
    "vm_ssh_users": {},
    "vm_os_label": {},
    "vm_cloudinit": {},
    "vm_auto_cleanup": {"volatile": {"last_active_at", "warned_at"}, "fill": {"last_active_at": "now"}},
    "container_ssh_users": {},
    "container_storage": {},
    "container_apps": {},
    # Storage and network
    "shared_pools": {},
    "network_firewall": {},
    # Protection and automation
    "backup_jobs": {"volatile": {"derniere_execution"}},
    "backup_group_jobs": {"volatile": {"derniere_execution"}},
    "replication_jobs": {"volatile": {"derniere_execution"}},
    "jobs": {},
    "job_steps": {},
    # Integrations
    "notification_channels": {"priv": True},
    "metric_servers": {"priv": True, "volatile": {"dernier_envoi", "derniere_erreur"}},
    "k8s_clusters": {"priv": True, "volatile": {"erreur", "task_id"}},
}
