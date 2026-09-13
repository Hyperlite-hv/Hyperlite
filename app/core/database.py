import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent.parent / "hyperlite.db"


@contextmanager
def get_conn():
    # timeout=30 (au lieu du defaut de 5s) + mode WAL : corrige un vrai
    # `database is locked` rencontre a plusieurs reprises en pratique
    # (chantier 11, reconfirme au chantier 13) des que deux ecritures
    # concurrentes se chevauchent -- le service ecrit en continu (audit,
    # taches, metriques toutes les 15s). WAL permet aux lecteurs de
    # continuer pendant qu'un writer est actif (contrairement au mode
    # rollback-journal par defaut, qui verrouille tout le fichier) ; le
    # PRAGMA est un no-op si deja applique, sans cout a le repeter a chaque
    # connexion.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'observateur'))
            )
        """)
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
                cible TEXT,
                node TEXT,
                username TEXT,
                statut TEXT NOT NULL CHECK(statut IN ('en_attente', 'en_cours', 'termine', 'echec')),
                progres INTEGER NOT NULL DEFAULT 0,
                cree_le TEXT NOT NULL,
                debut_le TEXT,
                fin_le TEXT,
                erreur TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_cree_le ON tasks(cree_le)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN ('raw', 'hourly')),
                scope TEXT NOT NULL CHECK(scope IN ('vm', 'host')),
                cible TEXT NOT NULL,
                cpu_pct REAL,
                mem_used_mb REAL,
                mem_total_mb REAL,
                disk_read_bps REAL,
                disk_write_bps REAL,
                net_rx_bps REAL,
                net_tx_bps REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_cible_ts ON metrics_samples(cible, tier, ts)")

        # ---- Backups natifs (chantier 13) ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vm_name TEXT NOT NULL UNIQUE,
                frequence TEXT NOT NULL CHECK(frequence IN ('quotidien', 'hebdomadaire', 'mensuel')),
                heure TEXT NOT NULL,
                cible_dir TEXT NOT NULL,
                retention_count INTEGER NOT NULL DEFAULT 7,
                actif INTEGER NOT NULL DEFAULT 1,
                derniere_execution TEXT,
                prochaine_execution TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vm_name TEXT NOT NULL,
                job_id INTEGER,
                chemin TEXT NOT NULL,
                taille_octets INTEGER,
                checksum_sha256 TEXT,
                mode TEXT NOT NULL CHECK(mode IN ('chaud', 'froid')),
                cree_le TEXT NOT NULL,
                statut TEXT NOT NULL CHECK(statut IN ('en_cours', 'termine', 'echec')),
                task_id TEXT,
                erreur TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backups_vm ON backups(vm_name, cree_le)")

        # ---- Automation : moteur de jobs (chantier 14) ----
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
                ordre INTEGER NOT NULL,
                cible_type TEXT NOT NULL CHECK(cible_type IN ('vm', 'host', 'chaque_cible')),
                cible TEXT,
                commande TEXT NOT NULL,
                condition_type TEXT NOT NULL DEFAULT 'exit_code' CHECK(condition_type IN ('exit_code', 'stdout_contains')),
                condition_valeur TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                task_id TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                targets TEXT,
                statut TEXT NOT NULL CHECK(statut IN ('en_cours', 'succes', 'echec')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                resultat TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES job_runs(id),
                step_ordre INTEGER,
                cible TEXT,
                commande TEXT,
                stdout TEXT,
                stderr TEXT,
                exit_code INTEGER,
                reussi INTEGER,
                horodatage TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_id, started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_run_logs_run ON job_run_logs(run_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_ssh_users (
                vm_name TEXT PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)
        # Suivi d'une installation automatisee (Kickstart/autoinstall) en
        # cours : cree a la creation de la VM, supprime des que le terminal
        # SSH web repond -- sert uniquement a afficher une barre de
        # progression cote dashboard (voir GET /vms/{name}/provisioning).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_provisioning (
                vm_name TEXT PRIMARY KEY,
                os_family TEXT NOT NULL,
                started_at TEXT NOT NULL
            )
        """)
        # ALTER separe (pas dans le CREATE TABLE ci-dessus) : la table existe
        # deja sur les installs anterieures au chantier 12, CREATE TABLE IF
        # NOT EXISTS ne retro-ajoute pas de colonne a une table deja creee.
        try:
            conn.execute("ALTER TABLE vm_provisioning ADD COLUMN task_id TEXT")
        except sqlite3.OperationalError:
            pass  # colonne deja presente
        # Libelle d'OS DECLARE a la creation de la VM (deduit du template/ISO
        # choisi, voir vms.create_vm) -- pas "detecte" au sens propre (pas de
        # qemu-guest-agent installe dans les VM invitees aujourd'hui, donc
        # libvirt ne peut rien lire depuis l'interieur), mais fiable puisque
        # c'est Hyperlite lui-meme qui a lance cette installation et sait
        # quel OS il a demande.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_os_label (
                vm_name TEXT PRIMARY KEY,
                os_label TEXT NOT NULL
            )
        """)

        # ---- Permissions granulaires (voir app/core/permissions.py) ----
        # Groupes d'utilisateurs, pools de VM, et attributions (ACL) : un
        # role scope (operateur/gestionnaire/lecteur, distincts des roles
        # globaux admin/observateur) accorde a un utilisateur OU un groupe,
        # sur une VM OU un pool precis. Additif uniquement -- n'enleve jamais
        # de droits aux roles globaux existants.
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
                resource_type TEXT NOT NULL CHECK(resource_type IN ('vm','pool')),
                resource_id TEXT NOT NULL
            )
        """)
        # Roles personnalises : memes attributions ACL que les roles predefinis
        # (lecteur/operateur/gestionnaire), mais l'utilisateur choisit lui-meme
        # le sous-ensemble de privileges (voir app/core/permissions.py
        # ALL_PRIVILEGES). Identifies dans acl.role par "custom:<id>".
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                privileges TEXT NOT NULL
            )
        """)
        conn.commit()
