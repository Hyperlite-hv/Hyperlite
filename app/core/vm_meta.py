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
