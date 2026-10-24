import contextlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.core import paths
from app.repositories.cfs import ids as _ids

DB_PATH = Path(os.environ.get("HYPERLITE_DB_PATH") or paths.DB)


@contextmanager
def get_conn():
    # timeout=30 (instead of the default 5 s) plus WAL mode: fixes a real
    # `database is locked` seen repeatedly in practice as soon as two concurrent
    # writes overlap, since the service writes continuously (audit, tasks, metrics
    # every 15 s). WAL lets readers continue while a writer is active (unlike the
    # default rollback-journal mode, which locks the whole file). The PRAGMA is a
    # no-op when already applied, so repeating it on every connection costs nothing.
    # ids.Connection: in a cluster, a new row of a replicated table gets an id no other node hands out.
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=_ids.Connection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    # Root only, as Proxmox VEs config.db: it holds every accounts password hash. Made so before SQLite creates it
    # with the default mode; its -wal and -shm files take the same.
    with contextlib.suppress(OSError):
        os.close(os.open(DB_PATH, os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(DB_PATH, 0o600)
    with get_conn() as conn:
        # API v1: the old French names renamed first, before any statement below uses the new ones (app/core/schema_v1.py).
        from app.core import schema_v1

        schema_v1.migrate(conn, DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'viewer'))
            )
        """)
        # TOTP 2FA: a separate ALTER, because `users` already exists in deployed
        # databases (same reason as vm_provisioning/task_id below). totp_secret stays
        # NULL until 2FA is both configured and confirmed (see app/core/twofa.py): a
        # secret that was generated purpose never confirmed by a real code must NOT enable
        # 2FA, otherwise a user who never finished the QR code step would be locked out
        # of their own account.
        for ddl in (
            "ALTER TABLE users ADD COLUMN totp_secret TEXT",
            "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0",
            # SSO: 'local' (Hyperlite password, the historical behaviour) or 'sso'
            # (provisioned automatically by app/core/sso.py, with the local password made
            # unusable and the role re-resolved at every login from the IdP groups).
            # Distinguishing the two is essential to NEVER let an SSO login overwrite an
            # existing local account (see sso.py::provision_user): the local admin must stay
            # a reliable fallback even if the IdP is misconfigured.
            "ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local'",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT,
                action TEXT NOT NULL,
                resource TEXT,
                result TEXT NOT NULL,
                error_message TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                target TEXT,
                node TEXT,
                username TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed')),
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_cree_le ON tasks(created_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN ('raw', 'hourly')),
                scope TEXT NOT NULL CHECK(scope IN ('vm', 'host')),
                target TEXT NOT NULL,
                cpu_pct REAL,
                mem_used_mb REAL,
                mem_total_mb REAL,
                disk_read_bps REAL,
                disk_write_bps REAL,
                net_rx_bps REAL,
                net_tx_bps REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_cible_ts ON metrics_samples(target, tier, ts)")

        # ---- Security keys and passkeys (WebAuthn, see app/core/webauthn_keys.py) ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webauthn_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                credential_id TEXT NOT NULL UNIQUE,
                public_key TEXT NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                rp_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(username, rp_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webauthn_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                purpose TEXT NOT NULL,
                challenge TEXT NOT NULL,
                rp_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        # Last copy of the configuration to each node (app/core/config_copy.py).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_copies (
                node TEXT PRIMARY KEY,
                copied_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ok', 'failed')),
                size INTEGER,
                fingerprint TEXT,
                error TEXT
            )
        """)

        # ---- Native backups ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vm_name TEXT NOT NULL UNIQUE,
                frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'monthly')),
                time TEXT NOT NULL,
                target_dir TEXT NOT NULL,
                retention_count INTEGER NOT NULL DEFAULT 7,
                active INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                next_run TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vm_name TEXT NOT NULL,
                job_id INTEGER,
                path TEXT NOT NULL,
                size_bytes INTEGER,
                checksum_sha256 TEXT,
                mode TEXT NOT NULL CHECK(mode IN ('live', 'cold')),
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                task_id TEXT,
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backups_vm ON backups(vm_name, created_at)")
        # Integrity (app/core/backup_integrity.py): NULL until the first verification, then 'verified' or 'corrupted'
        # with the date and what was wrong.
        for ddl in (
            "ALTER TABLE backups ADD COLUMN verification TEXT",
            "ALTER TABLE backups ADD COLUMN verified_at TEXT",
            "ALTER TABLE backups ADD COLUMN verification_detail TEXT",
            # GFS retention (app/core/backup_retention.py): NULL keeps the plain count of retention_count.
            "ALTER TABLE backup_jobs ADD COLUMN keep_days INTEGER",
            "ALTER TABLE backup_jobs ADD COLUMN keep_weeks INTEGER",
            "ALTER TABLE backup_jobs ADD COLUMN keep_months INTEGER",
            # A backup made by a grouped job (backup_group_jobs), NULL otherwise.
            "ALTER TABLE backups ADD COLUMN group_id INTEGER",
            # A backup made by another Hyperlite (another site) and registered here to be restored: the host that
            # made it, from its manifest. NULL for this node's own backups.
            "ALTER TABLE backups ADD COLUMN imported_from TEXT",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        # Grouped backup jobs (app/core/backup_groups.py): one schedule for every VM of this node, of a tag or of a
        # pool, resolved at each run so new VMs are covered without editing the job.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_group_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                selection TEXT NOT NULL CHECK(selection IN ('all', 'tag', 'pool')),
                value TEXT,
                excluded TEXT NOT NULL DEFAULT '[]',
                frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'monthly')),
                time TEXT NOT NULL,
                target_dir TEXT NOT NULL,
                retention_count INTEGER NOT NULL DEFAULT 7,
                keep_days INTEGER,
                keep_weeks INTEGER,
                keep_months INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                next_run TEXT NOT NULL
            )
        """)
        # Backup stores (app/core/backup_stores.py, docs/design/backup-store.md): a store lives in a directory of a
        # storage pool, the same on every node that mounts the pool. `store_id` is the one its .store.json holds.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_stores (
                name TEXT PRIMARY KEY,
                pool TEXT NOT NULL,
                directory TEXT NOT NULL,
                store_id TEXT NOT NULL,
                encrypted INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT
            )
        """)
        # The key of an encrypted store, encrypted at rest (app/core/secrets_crypto.py), replicated under /priv (root
        # only). Kept apart from backup_stores, whose rows every reader of the configuration sees.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_store_keys (
                name TEXT PRIMARY KEY,
                key_encrypted TEXT NOT NULL,
                created_at TEXT
            )
        """)
        # Copies of a store into another (app/core/backup_store_sync.py): pulled by the copy's side on its own
        # schedule, with a retention of its own (a deletion at the source never reaches the copy).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_store_syncs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                frequency TEXT NOT NULL CHECK(frequency IN ('daily', 'weekly', 'monthly')),
                time TEXT NOT NULL,
                retention_count INTEGER NOT NULL DEFAULT 7,
                keep_days INTEGER,
                keep_weeks INTEGER,
                keep_months INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                last_result TEXT,
                next_run TEXT NOT NULL
            )
        """)
        # Other Hyperlite clusters whose stores are copied here (app/core/backup_remotes.py): an address, its pinned
        # certificate, a reader's API token encrypted at rest; replicated under /priv.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_remotes (
                name TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                port INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                token_encrypted TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT
            )
        """)
        # A backup or a job into a store: its name; NULL for a backup directory (the format of every backup before).
        for ddl in (
            # A store made as the copy of another (same chunk size and key): the source's name.
            "ALTER TABLE backup_stores ADD COLUMN copy_of TEXT",
            # The public half of a store's master recovery key (PEM), NULL without one.
            "ALTER TABLE backup_stores ADD COLUMN master_public_key TEXT",
            "ALTER TABLE backups ADD COLUMN store TEXT",
            "ALTER TABLE backup_jobs ADD COLUMN store TEXT",
            "ALTER TABLE backup_group_jobs ADD COLUMN store TEXT",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        # In a cluster, this node's own next run of the jobs every node runs for its VMs (grouped backups,
        # replication), against the shared value it was computed from: app/repositories/sqlite/schedule_state.py.
        # Each node keeps its own (not in app/repositories/cfs/tables.py).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedule_state (
                kind TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                base TEXT NOT NULL,
                next_run TEXT NOT NULL,
                PRIMARY KEY (kind, job_id)
            )
        """)

        # ---- Replication to another site (app/core/replication.py): incremental copies every few minutes ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS replication_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                selection TEXT NOT NULL CHECK(selection IN ('all', 'tag', 'pool')),
                value TEXT,
                excluded TEXT NOT NULL DEFAULT '[]',
                target_dir TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL DEFAULT 15,
                active INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                next_run TEXT NOT NULL
            )
        """)
        # One row per replicated VM: where its current chain is and how its last copy went.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS replication_state (
                vm_name TEXT PRIMARY KEY,
                target_dir TEXT NOT NULL,
                chain TEXT,
                last_point TEXT,
                checkpoint TEXT,
                points INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT,
                -- The disks' newest modification time right after the last copy of a stopped VM: the same value at
                -- the next run proves nothing started it since (QEMU rewrites the file at least when it stops).
                disks_mtime REAL,
                status TEXT NOT NULL DEFAULT 'never',
                error TEXT,
                updated_at TEXT
            )
        """)

        # ---- Automation: job engine ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                predefined_key TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                position INTEGER NOT NULL,
                target_type TEXT NOT NULL CHECK(target_type IN ('vm', 'host', 'each_target')),
                target TEXT,
                command TEXT NOT NULL,
                condition_type TEXT NOT NULL DEFAULT 'exit_code' CHECK(condition_type IN ('exit_code', 'stdout_contains')),
                condition_value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                task_id TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                targets TEXT,
                status TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                result TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES job_runs(id),
                step_position INTEGER,
                target TEXT,
                command TEXT,
                stdout TEXT,
                stderr TEXT,
                exit_code INTEGER,
                succeeded INTEGER,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_id, started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_run_logs_run ON job_run_logs(run_id)")

        # ---- Multi-node ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                hostname TEXT NOT NULL,
                ssh_user TEXT NOT NULL DEFAULT 'root',
                ssh_port INTEGER NOT NULL DEFAULT 22,
                status TEXT NOT NULL DEFAULT 'unknown' CHECK(status IN ('online', 'offline', 'unknown')),
                last_verification TEXT,
                added_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_ssh_users (
                vm_name TEXT PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)
        # The tracking of an unattended installation (Kickstart/autoinstall), which Hyperlite no longer does since
        # 1.0 (an ISO is installed by hand, as on Proxmox VE): nothing writes it any more; the table stays so that
        # the databases of earlier versions open and migrate unchanged.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_provisioning (
                vm_name TEXT PRIMARY KEY,
                os_family TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
        """)
        # A separate ALTER (not in the CREATE TABLE above): the table already exists on
        # older installations, and CREATE TABLE IF NOT EXISTS does not retroactively add
        # a column to a table that was already created.
        with contextlib.suppress(sqlite3.OperationalError):  # column already exists
            conn.execute("ALTER TABLE vm_provisioning ADD COLUMN task_id TEXT")
        # OS label DECLARED when the VM is created (deduced from the chosen
        # template/ISO, see vms.create_vm). It is not "detected" in the strict sense (no
        # qemu-guest-agent is installed in guest VMs, so libvirt cannot read anything from
        # the inside), purpose it is reliable because Hyperlite itself started that
        # installation and knows which OS it asked for.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_os_label (
                vm_name TEXT PRIMARY KEY,
                os_label TEXT NOT NULL
            )
        """)

        # ---- Granular permissions (see app/core/permissions.py) ----
        # User groups, VM pools and assignments (ACLs): a scoped role
        # (operator/manager/reader, distinct from the global admin/observer roles) granted
        # to a user OR a group on a specific VM OR pool. Additive only: it never removes
        # rights from the existing global roles.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                PRIMARY KEY (group_id, username)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pool_members (
                pool_id INTEGER NOT NULL,
                vm_name TEXT NOT NULL,
                PRIMARY KEY (pool_id, vm_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS acl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL CHECK(subject_type IN ('user','group')),
                subject_id TEXT NOT NULL,
                role TEXT NOT NULL,
                resource_type TEXT NOT NULL CHECK(resource_type IN ('vm','pool','container','storage','network','backup_store')),
                resource_id TEXT NOT NULL
            )
        """)
        # Migration: existing databases were created with an older CHECK (resource_type IN ('vm','pool'), then
        # ('vm','pool','container'), then without 'backup_store'). CREATE TABLE IF NOT EXISTS above is a no-op on a
        # table that already exists, and SQLite cannot modify an existing CHECK through ALTER TABLE. The table is rebuilt (idempotent: does nothing if already
        # migrated).
        existing_acl_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='acl'").fetchone()
        if existing_acl_sql and "'backup_store'" not in existing_acl_sql["sql"]:
            conn.execute("ALTER TABLE acl RENAME TO acl_pre_container_migration")
            conn.execute("""
                CREATE TABLE acl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_type TEXT NOT NULL CHECK(subject_type IN ('user','group')),
                    subject_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    resource_type TEXT NOT NULL CHECK(resource_type IN ('vm','pool','container','storage','network','backup_store')),
                    resource_id TEXT NOT NULL
                )
            """)
        # The rows are copied with their ids: nothing to reserve from the cluster (app/repositories/cfs/ids.py), which
        # may still be agreeing on its state as the service starts after an update. Reserving one failed there, and a
        # start that stopped between the two statements left the assignments in the old table, unread: a start that
        # finds it finishes the copy.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acl_pre_container_migration'"
        ).fetchone():
            reserve_ids, conn.reserve_ids = conn.reserve_ids, False
            try:
                conn.execute("INSERT OR IGNORE INTO acl SELECT * FROM acl_pre_container_migration")
            finally:
                conn.reserve_ids = reserve_ids
            conn.execute("DROP TABLE acl_pre_container_migration")
        # Custom roles: the same ACL assignments as the predefined roles
        # (reader/operator/manager), purpose the user chooses the privilege subset (see
        # ALL_PRIVILEGES in app/core/permissions.py). They are identified in acl.role by
        # "custom:<id>".
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                privileges TEXT NOT NULL
            )
        """)
        # LXC containers: a table distinct from vm_ssh_users. Qemu and lxc domains live
        # in separate libvirt namespaces (see open_lxc_conn), so a container and a VM can
        # in theory share the same name with no collision to avoid here.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS container_ssh_users (
                container_name TEXT PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)
        # Where a container's filesystem lives when it is not the default /var/lib/libvirt/containers
        # (app/core/container_builder.py::container_rootfs_path): a "dir" storage pool chosen at creation.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS container_storage (
                container_name TEXT PRIMARY KEY,
                pool TEXT NOT NULL,
                base_dir TEXT NOT NULL
            )
        """)
        # Containers that run their image's own process (app/core/container_meta.py::set_container_app).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS container_apps (
                container_name TEXT PRIMARY KEY,
                image TEXT NOT NULL,
                spec TEXT NOT NULL,
                ip TEXT,
                network TEXT
            )
        """)
        # HA: "protected" VMs. domain_xml is a CACHE refreshed periodically (see
        # app/core/ha.py::sync_protected_vms) WHILE the source node is reachable, the only
        # way to redefine the VM elsewhere if that node really fails (its XML can no longer
        # be requested once it is unreachable). Protection REQUIRES shared storage,
        # checked at activation AND at every resynchronization: without it there is no
        # guarantee that the disk is even readable from another node.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ha_protected_vms (
                vm_name TEXT PRIMARY KEY,
                node TEXT NOT NULL,
                domain_xml TEXT,
                enabled_by TEXT NOT NULL,
                enabled_at TEXT NOT NULL,
                last_synced_at TEXT
            )
        """)
        # HA dry run (app/core/ha_watch.py): the watcher's view of each protected VM, and what automatic HA would
        # have done when its node failed.
        for ddl in (
            "ALTER TABLE ha_protected_vms ADD COLUMN ha_state TEXT",
            "ALTER TABLE ha_protected_vms ADD COLUMN last_action TEXT",
            "ALTER TABLE ha_protected_vms ADD COLUMN last_action_at TEXT",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        # Automatic HA per protected VM (docs/design/ha-automatic.md, 4.3): its restart priority, its preferred nodes
        # (a JSON list, in order) and its last automatic restarts (a JSON list of times), for the limit per hour. A
        # table of its own, not columns of ha_protected_vms: a node before this version ignores a table it does not
        # know, while a column it does not know would stop it applying any change from the cluster.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ha_policies (
                vm_name TEXT PRIMARY KEY,
                priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('high', 'medium', 'low')),
                preferred_nodes TEXT NOT NULL DEFAULT '[]',
                auto_restarts TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # VM monitoring of an HA VM (docs/design/ha-automatic.md, 4.9): a table of its own, for the same reason.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ha_monitoring (
                vm_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                resets TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # Fencing settings per node ("local" or a registered node name), see app/core/ha_fencing.py. The password
        # is Fernet-encrypted and never returned by the API.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_fencing (
                node TEXT PRIMARY KEY,
                method TEXT NOT NULL CHECK(method IN ('ipmi', 'redfish', 'amt', 'lease_only')),
                address TEXT,
                port INTEGER,
                user TEXT,
                secret TEXT,
                tls_unverified INTEGER NOT NULL DEFAULT 0,
                modified_by TEXT,
                modified_at TEXT
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS ha_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Storage shared by several nodes (app/core/shared_pools.py): created on each of them, and on the nodes
        # registered later when declared for every node. definition is JSON, its CHAP password encrypted.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_pools (
                name TEXT PRIMARY KEY,
                definition TEXT NOT NULL,
                all_nodes INTEGER NOT NULL DEFAULT 0,
                nodes TEXT NOT NULL DEFAULT '[]',
                created_by TEXT,
                created_at TEXT
            )
        """)
        # Settings of the application itself, one value per key (app/core/api_docs.py...).
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Nodes in maintenance: "local" (the host running Hyperlite, not a row of `nodes`) or a registered node
        # name. No new VM lands on them and they are never a migration or HA recovery target.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_maintenance (
                node TEXT PRIMARY KEY,
                started_by TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
        """)
        # Outbound notifications: the JSON config is stored in clear text (including
        # the SMTP password when type='email'), except that the SMTP password is
        # encrypted at rest (see app/core/secrets_crypto.py). Admin-only. `events` is a
        # JSON list of event names to notify on this channel, [] = all (see
        # app/core/notifications.py::NOTIFY_EVENTS for the full list).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('webhook', 'email')),
                name TEXT NOT NULL,
                config TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # API tokens: a credential dedicated to automation (scripts/Terraform), separate
        # from the session JWT (different lifetime and scope: an API token does not
        # expire after 4 h like a session, purpose can be revoked individually without
        # signing the user out everywhere). ONLY token_hash (SHA-256) is stored, NEVER
        # the plain token: it is shown only ONCE, at creation (see
        # app/core/api_tokens.py), and cannot be recovered afterwards even by an admin
        # with direct access to the database.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
        """)
        # Workstation tokens (`hyperlite login`) expire; tokens created by hand keep no
        # expiry unless one is set. kind tells them apart in the token list.
        for ddl in (
            "ALTER TABLE api_tokens ADD COLUMN expires_at TEXT",
            "ALTER TABLE api_tokens ADD COLUMN kind TEXT NOT NULL DEFAULT 'api'",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        # Network/datacenter firewall: unlike the per-VM firewall (nwfilter, stored and
        # reapplied by libvirt itself), these iptables rules do NOT survive a host
        # reboot. This table is the only persistent source of truth, reapplied when the
        # service starts (see app/core/network_firewall.py::reapply_all).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_firewall (
                network_name TEXT PRIMARY KEY,
                default_policy TEXT NOT NULL,
                rules_json TEXT NOT NULL
            )
        """)
        # Automatic deletion of inactive VMs: an opt-in option chosen at creation
        # ("delete if stopped for N days"). last_active_at is reset every time the VM
        # starts (see app/core/vm_meta.py::touch_vm_activity): the counter only runs while
        # the VM is STOPPED. warned_at records a warning that was already sent, so it is
        # not repeated at every hourly scheduler cycle before the real deletion.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_auto_cleanup (
                vm_name TEXT PRIMARY KEY,
                inactive_days INTEGER NOT NULL,
                last_active_at TEXT NOT NULL,
                warned_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # Brute-force lock (app/core/login_guard.py): one row per failed password or 2FA code, per account
        # (kind 'user') and per client address (kind 'ip'). Kept here so a restart does not reset the lock.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_failures (
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_failures ON login_failures (kind, key, at)")
        # Automatic periodic update check (app/core/update_check.py): a SINGLE row
        # (id=1) that remembers the last remote version already notified, so that only
        # ONE notification is sent per available version instead of one per hourly cycle
        # while nobody applies the update.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS update_check_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_notified_version TEXT,
                last_checked_at TEXT
            )
        """)
        # OIDC SSO: a SINGLE row (id=1), the same pattern as update_check_state above.
        # client_secret is encrypted at rest (see app/core/secrets_crypto.py). Admin-only.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                issuer TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '',
                client_secret TEXT NOT NULL DEFAULT '',
                redirect_uri TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'openid profile email groups',
                group_claim TEXT NOT NULL DEFAULT 'groups',
                admin_groups TEXT NOT NULL DEFAULT ''
            )
        """)
        # CSRF/nonce states of the OIDC Authorization Code flow: SINGLE USE (deleted as
        # soon as consumed, see sso.py::consume_state) and short-lived (STATE_TTL_S,
        # purged along the way rather than by a dedicated scheduler for such an ephemeral
        # table).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_login_state (
                state TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        # Container backups: a deliberately simpler version than `backups` (VMs): no
        # job_id, no scheduling and no hot/cold mode. A container must always be STOPPED
        # to be backed up (a filesystem, not a qcow2 disk that can be copied while
        # running). Manual only for now.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS container_backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                container_name TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes INTEGER,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                task_id TEXT,
                error TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_container_backups_name ON container_backups(container_name, created_at)"
        )
        # Latest live figures of every node ('local' = this host), refreshed by the
        # metrics collector: the node list and the dashboard read them instead of
        # opening an SSH connection per node on every page load.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS node_live (
                name TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                reachable INTEGER NOT NULL DEFAULT 1,
                cpu_pct REAL,
                mem_used_mb REAL,
                mem_total_mb REAL,
                uptime_s INTEGER,
                cores INTEGER,
                cpu_model TEXT,
                kernel TEXT,
                os TEXT,
                address TEXT,
                hypervisor_version INTEGER,
                libvirt_version INTEGER
            )
        """)
        # Storage pool usage over time (same raw/hourly tiers as metrics_samples).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS storage_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN ('raw', 'hourly')),
                node TEXT NOT NULL,
                pool TEXT NOT NULL,
                capacity_b REAL,
                allocation_b REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_storage_samples ON storage_samples(node, pool, tier, ts)")
        # Kubernetes (k3s) clusters built on Hyperlite VMs (app/core/k8s_cluster.py). The join token and the
        # kubeconfig are stored encrypted (app/core/secrets_crypto.py).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS k8s_clusters (
                name TEXT PRIMARY KEY,
                version TEXT,
                network TEXT NOT NULL,
                server TEXT NOT NULL,
                workers TEXT NOT NULL,
                vcpu INTEGER NOT NULL,
                memory_mb INTEGER NOT NULL,
                disk_gb INTEGER NOT NULL,
                address TEXT,
                status TEXT NOT NULL CHECK(status IN ('creating', 'ready', 'failed', 'deleting')),
                error TEXT,
                token TEXT,
                kubeconfig TEXT,
                task_id TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL
            )
        """)
        for ddl in (
            "ALTER TABLE users ADD COLUMN last_login_at TEXT",
            # Epoch second of the last password change or reset (NULL: never): session tokens issued
            # before it are refused (app/core/security.py::set_password, _session_user).
            "ALTER TABLE users ADD COLUMN password_changed_at INTEGER",
            # 1 when the password no longer meets the policy (seen at sign-in, the only moment the plain password
            # is known): the account can do nothing purpose change it (app/core/security.py::_session_user).
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
            # Source address of the request that produced the audit entry (NULL for
            # background jobs, which have no request).
            "ALTER TABLE audit_log ADD COLUMN ip TEXT",
            # Last TOTP time step accepted for the account: a code is accepted once (app/core/twofa.py).
            "ALTER TABLE users ADD COLUMN totp_last_step INTEGER",
            # The identity provider's stable identifier (`sub`) of an SSO account (app/core/sso.py).
            "ALTER TABLE users ADD COLUMN sso_subject TEXT",
            # The browser a sign-in started from: the callback must come back to it (app/core/sso.py).
            "ALTER TABLE sso_login_state ADD COLUMN binding TEXT",
        ):
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(ddl)
        # Session tokens signed out before their expiry (POST /auth/logout); rows are dropped once expired.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS revoked_sessions (
                jti TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            )
        """)
        # One-time codes that hand an SSO sign-in over to the dashboard (the session token never travels in
        # a URL): single use, a minute at most.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_handoffs (
                code TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        # Start at boot (app/core/vm_boot.py): per node and VM, and the last boot of each node that was handled.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_boot (
                node TEXT NOT NULL DEFAULT 'local',
                vm_name TEXT NOT NULL,
                autostart INTEGER NOT NULL DEFAULT 0,
                boot_order INTEGER,
                delay_s INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (node, vm_name)
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS vm_boot_state (node TEXT PRIMARY KEY, boot_id TEXT NOT NULL)")
        # Task log lines (app/core/tasks.py) and who cancelled a task.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                at TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id)")
        with contextlib.suppress(sqlite3.OperationalError):  # column already exists
            conn.execute("ALTER TABLE tasks ADD COLUMN cancelled_by TEXT")
        # Cloud-init edited after creation (app/core/cloudinit_edit.py): the account and keys last written, never a
        # password.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_cloudinit (
                vm_name TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                ssh_keys TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)
        # What else Hyperlite wrote on a VM's cloud-init drive (app/core/cloudinit_edit.py, CUSTOM): host name, domain,
        # static addresses, resolvers, a cloud-config of the user's, as JSON. A table of its own, not columns of
        # vm_cloudinit: hyperlite-cfs copies a row with all its columns, and a member still on an older version would
        # refuse a column it does not have, whereas it leaves a table it does not know alone.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_cloudinit_custom (
                vm_name TEXT PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
        """)
        # Notes and tags (app/core/object_meta.py). node is '' for a node itself, the VM's node otherwise.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS object_meta (
                kind TEXT NOT NULL,
                node TEXT NOT NULL,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (kind, node, name)
            )
        """)
        # LDAP / Active Directory sign-in (app/core/ldap_auth.py): one directory; the bind password is encrypted.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ldap_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                url TEXT NOT NULL DEFAULT '',
                starttls INTEGER NOT NULL DEFAULT 0,
                verify_tls INTEGER NOT NULL DEFAULT 1,
                ca_cert TEXT NOT NULL DEFAULT '',
                bind_dn TEXT NOT NULL DEFAULT '',
                bind_password TEXT NOT NULL DEFAULT '',
                base_dn TEXT NOT NULL DEFAULT '',
                user_filter TEXT NOT NULL DEFAULT '(&(objectClass=person)(|(uid={username})(sAMAccountName={username})))',
                group_attribute TEXT NOT NULL DEFAULT 'memberOf',
                admin_groups TEXT NOT NULL DEFAULT '',
                allowed_groups TEXT NOT NULL DEFAULT ''
            )
        """)
        with contextlib.suppress(sqlite3.OperationalError):  # column already exists
            conn.execute("ALTER TABLE users ADD COLUMN ldap_dn TEXT")
        # Metric servers the collector pushes each sample to (app/core/metric_export.py); the token is encrypted.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metric_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                url TEXT,
                host TEXT,
                port INTEGER,
                org TEXT,
                bucket TEXT,
                token TEXT,
                prefix TEXT NOT NULL DEFAULT 'hyperlite',
                active INTEGER NOT NULL DEFAULT 1,
                last_sent_at TEXT,
                last_error TEXT
            )
        """)
        # The local host used to be stored under a machine-specific label; it is now always "local".
        for table in ("ha_protected_vms", "tasks"):
            conn.execute(f"UPDATE {table} SET node = 'local' WHERE node = 'kvm-lab'")  # noqa: S608
        # Last, once every table exists: the triggers that record configuration changes for hyperlite-cfs, then the
        # node's own name in the tables every node of a cluster shares (the triggers record that rewrite).
        from app.core import cluster_setup, self_node
        from app.repositories.cfs.shadow import install_triggers

        install_triggers(conn)
        _ids.forget()  # the numbered tables are known once they all exist
        self_node.migrate(conn)
        cluster_setup.finish_join(conn)
        # Successful reads are no longer logged (audit.READ_ACTION_PREFIXES); those logged before drowned the real
        # actions (102,073 of 102,926 entries on a host audited before 1.0.0). Nothing writes them any more, so this
        # finds nothing after the first start.
        conn.execute(
            "DELETE FROM audit_log WHERE result = 'success' AND (action LIKE 'list\\_%' ESCAPE '\\' "
            "OR action LIKE 'get\\_%' ESCAPE '\\')"
        )
        conn.commit()
