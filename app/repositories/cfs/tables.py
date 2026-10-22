"""The SQLite tables hyperlite-cfs holds (docs/design/hyperlite-cfs.md, phases C and D): configuration only.

Each entry names the columns left out because they change all the time and say nothing about the configuration (a
last run, a last check, a counter), whether the table holds secrets (copied under /priv, which the daemon serves to
root only), and rows that stay local. Tables not listed are state or history (audit log, tasks, metrics, backup
records, sign-in failures, challenges): each node keeps its own.

A left-out column must be nullable or have a default, or be given a value in "fill" ("now": the current time), since a
row that comes from another node is inserted without it (tests/test_cfs_shadow.py checks this).

A column added to a listed table is copied without changing this file; a new table is copied only once listed here.
"""

TABLES = {
    # Accounts and access
    "users": {"priv": True, "volatile": {"last_login_at", "totp_last_step"}},
    "webauthn_credentials": {"priv": True, "volatile": {"sign_count", "used_at"}},
    "api_tokens": {"priv": True, "volatile": {"last_used_at"}},
    # A sign-out revokes its session on every node: a session token is valid on any of them (the signing key is
    # shared), so a revocation only this node knew would leave it usable elsewhere until it expires.
    "revoked_sessions": {},
    "groups": {},
    "group_members": {},
    "custom_roles": {},
    "acl": {},
    "pools": {},
    "pool_members": {},
    "sso_config": {"priv": True},
    "ldap_config": {"priv": True},
    # Nodes and the cluster
    "nodes": {"volatile": {"status", "last_verification"}},
    "node_maintenance": {},
    "node_fencing": {"priv": True},
    "ha_settings": {},
    "ha_protected_vms": {"volatile": {"last_synced_at", "ha_state", "last_action", "last_action_at"}},
    # The switch of this very mirror, the cfs version this node last applied, the daemon's last mode, this node's own
    # name, its pending join, the ticket of its join information, the one-time rewrite of its tasks (they are its own
    # history) and its own step of a rolling update stay per node.
    "app_settings": {
        "local_rows": {
            "key": (
                "cfs_shadow",
                "cfs_applied_version",
                "cfs_mode",
                "node_name",
                "cfs_join_pending",
                "cluster_join_ticket",
                "tasks_node_names",
                "reboot_notified_boot",
                "rolling_update_self",
            )
        }
    },
    # Guests
    "object_meta": {},
    "vm_boot": {},
    "vm_ssh_users": {},
    "vm_os_label": {},
    "vm_cloudinit": {},
    "vm_cloudinit_custom": {},
    "vm_auto_cleanup": {"volatile": {"last_active_at", "warned_at"}, "fill": {"last_active_at": "now"}},
    "container_ssh_users": {},
    "container_storage": {},
    "container_apps": {},
    # Storage and network
    "shared_pools": {},
    "network_firewall": {},
    # Protection and automation
    "backup_jobs": {"volatile": {"last_run"}},
    "backup_group_jobs": {"volatile": {"last_run"}},
    "replication_jobs": {"volatile": {"last_run"}},
    "jobs": {},
    "job_steps": {},
    # Integrations
    "notification_channels": {"priv": True},
    "metric_servers": {"priv": True, "volatile": {"last_sent_at", "last_error"}},
    "k8s_clusters": {"priv": True, "volatile": {"error", "task_id"}},
}
