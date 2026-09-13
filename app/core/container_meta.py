from app.core.database import get_conn


def set_container_ssh_user(container_name, username):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO container_ssh_users (container_name, username) VALUES (?, ?) "
            "ON CONFLICT(container_name) DO UPDATE SET username = excluded.username",
            (container_name, username),
        )
        conn.commit()


def get_container_ssh_user(container_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT username FROM container_ssh_users WHERE container_name = ?", (container_name,)
        ).fetchone()
        return row["username"] if row else None


def delete_container_ssh_user(container_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM container_ssh_users WHERE container_name = ?", (container_name,))
        conn.commit()
