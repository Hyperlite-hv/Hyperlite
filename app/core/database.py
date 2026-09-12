import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent.parent / "hyperlite.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
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
        conn.commit()
