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
        # 2FA TOTP (chantier 30) : ALTER separe, `users` existe deja sur ce
        # depot (meme raison que vm_provisioning/task_id plus bas) --
        # totp_secret reste NULL tant que le 2FA n'est ni configure ni
        # confirme (voir app/core/twofa.py : un secret genere mais jamais
        # confirme par un vrai code ne doit PAS activer le 2FA, sinon un
        # utilisateur qui n'a jamais fini l'etape QR code se retrouverait
        # verrouille hors de son compte).
        for ddl in (
            "ALTER TABLE users ADD COLUMN totp_secret TEXT",
            "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0",
            # SSO (chantier 20, 2026-09-17) : 'local' (mot de passe Hyperlite,
            # comportement historique) ou 'sso' (provisionne automatiquement
            # par app/core/sso.py -- mot de passe local rendu inutilisable,
            # role re-resolu a chaque connexion depuis les groupes de l'IdP).
            # Distinguer les deux est indispensable pour ne JAMAIS laisser
            # une connexion SSO ecraser un compte local existant (voir
            # sso.py::provision_user) -- l'admin local doit rester un
            # secours fiable meme si l'IdP est mal configure.
            "ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local'",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # colonne deja presente
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

        # ---- Multi-noeuds (chantier 15) ----
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                hostname TEXT NOT NULL,
                ssh_user TEXT NOT NULL DEFAULT 'root',
                ssh_port INTEGER NOT NULL DEFAULT 22,
                statut TEXT NOT NULL DEFAULT 'inconnu' CHECK(statut IN ('en_ligne', 'hors_ligne', 'inconnu')),
                derniere_verification TEXT,
                added_at TEXT NOT NULL
            )
        """)
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
        # Conteneurs LXC (chantier 18) : table distincte de vm_ssh_users --
        # domaines qemu et lxc vivent dans des espaces de noms libvirt
        # separes (voir open_lxc_conn), un conteneur et une VM peuvent en
        # theorie partager le meme nom sans collision a eviter ici.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS container_ssh_users (
                container_name TEXT PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)
        # HA (chantier 17) : VM "protegees" -- domain_xml est un CACHE
        # rafraichi periodiquement (voir app/core/ha.py::sync_protected_vms)
        # PENDANT que le nœud source est joignable, seul moyen de redefinir
        # la VM ailleurs si ce nœud tombe reellement en panne (on ne peut
        # plus lui demander son XML une fois injoignable). Protection
        # EXIGE un stockage partage (chantier 26) verifie a l'activation ET
        # a chaque resynchronisation -- sans ca, aucune garantie que le
        # disque soit seulement lisible depuis un autre nœud.
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
        # Notifications sortantes (chantier 28) : config JSON stockee en
        # clair (mot de passe SMTP inclus si type='email') -- aucune autre
        # forme de secret n'est chiffree dans ce projet (voir .env pour le
        # secret JWT par ex.), reserve aux admins (meme niveau de confiance
        # que le reste de la config serveur). `events` : liste JSON de noms
        # d'evenements a notifier sur ce canal, [] = tous (voir
        # app/core/notifications.py::NOTIFY_EVENTS pour la liste complete).
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
        # Jetons d'API (chantier 30) : credential dedie a l'automatisation
        # (scripts/Terraform), separe du JWT de session (duree de vie/portee
        # differente -- un jeton d'API n'expire pas au bout de 4h comme une
        # session, mais peut etre revoque individuellement sans deconnecter
        # l'utilisateur partout). SEUL token_hash (SHA-256) est stocke, JAMAIS
        # le jeton en clair -- il n'est affiche qu'UNE fois, a la creation
        # (voir app/core/api_tokens.py), impossible a retrouver ensuite meme
        # par un admin avec un acces direct a la base.
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
        # Pare-feu reseau/datacenter (chantier 21) : contrairement au
        # pare-feu par VM (nwfilter, stocke et reapplique par libvirt
        # lui-meme), les regles iptables de ce chantier ne survivent PAS a
        # un redemarrage de l'hote -- cette table est l'unique source de
        # verite persistante, reappliquee au demarrage du service (voir
        # app/core/network_firewall.py::reapply_all).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_firewall (
                network_name TEXT PRIMARY KEY,
                default_policy TEXT NOT NULL,
                rules_json TEXT NOT NULL
            )
        """)
        # Suppression automatique des VM inactives (chantier 19) : option
        # opt-in a la creation ("supprimer si arretee depuis N jours").
        # last_active_at reinitialise a chaque demarrage de la VM (voir
        # app/core/vm_meta.py::touch_vm_activity) -- le compteur ne court
        # que pendant que la VM est ARRETEE. warned_at trace un
        # avertissement deja envoye (chantier 28) pour ne pas le repeter a
        # chaque cycle horaire du scheduler avant la suppression reelle.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vm_auto_cleanup (
                vm_name TEXT PRIMARY KEY,
                inactive_days INTEGER NOT NULL,
                last_active_at TEXT NOT NULL,
                warned_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # Verification automatique et periodique des mises a jour
        # (app/core/update_check.py, 2026-09-17) -- ligne UNIQUE (id=1) :
        # memorise la derniere version distante deja notifiee, pour ne
        # notifier qu'UNE FOIS par version disponible plutot qu'a chaque
        # cycle horaire tant que personne n'a applique la mise a jour.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS update_check_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_notified_version TEXT,
                last_checked_at TEXT
            )
        """)
        # SSO OIDC (chantier 20, 2026-09-17) -- ligne UNIQUE (id=1), meme
        # pattern que update_check_state ci-dessus. client_secret stocke en
        # clair : meme niveau de confiance que le mot de passe SMTP du
        # chantier 28 (reserve admin, pas de coffre-fort de secrets dans ce
        # projet, voir CLAUDE.md).
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
        # Etats CSRF/nonce du flux OIDC Authorization Code -- a usage
        # UNIQUE (supprime des sa consommation, voir sso.py::consume_state)
        # et de courte duree de vie (STATE_TTL_S, purge au passage plutot
        # qu'un scheduler dedie pour une table aussi ephemere).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sso_login_state (
                state TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
