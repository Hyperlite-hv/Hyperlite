from datetime import datetime, timezone

from app.core.database import get_conn


def set_vm_ssh_user(vm_name, username):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_ssh_users (vm_name, username) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET username = excluded.username",
            (vm_name, username),
        )
        conn.commit()


def get_vm_ssh_user(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT username FROM vm_ssh_users WHERE vm_name = ?", (vm_name,)).fetchone()
        return row["username"] if row else None


def delete_vm_ssh_user(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_ssh_users WHERE vm_name = ?", (vm_name,))
        conn.commit()


def rename_vm_ssh_user(old_name, new_name):
    """Utilise par le clonage : le disque clone contient deja le cloud-init joue
    une fois sur la VM source, donc le meme utilisateur systeme y existe."""
    username = get_vm_ssh_user(old_name)
    if username:
        set_vm_ssh_user(new_name, username)


# ---- Libelle d'OS declare a la creation (voir database.py::vm_os_label) ----

def set_vm_os_label(vm_name, os_label):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_os_label (vm_name, os_label) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_label = excluded.os_label",
            (vm_name, os_label),
        )
        conn.commit()


def get_vm_os_label(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT os_label FROM vm_os_label WHERE vm_name = ?", (vm_name,)).fetchone()
        return row["os_label"] if row else None


def delete_vm_os_label(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_os_label WHERE vm_name = ?", (vm_name,))
        conn.commit()


def rename_vm_os_label(old_name, new_name):
    label = get_vm_os_label(old_name)
    if label:
        set_vm_os_label(new_name, label)


# ---- Suivi de progression d'une installation automatisee (ISO reconnu) ----

def mark_provisioning(vm_name, os_family):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_provisioning (vm_name, os_family, started_at) VALUES (?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_family = excluded.os_family, started_at = excluded.started_at",
            (vm_name, os_family, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_provisioning(vm_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT os_family, started_at FROM vm_provisioning WHERE vm_name = ?", (vm_name,)
        ).fetchone()
        return dict(row) if row else None


def clear_provisioning(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_provisioning WHERE vm_name = ?", (vm_name,))
        conn.commit()
