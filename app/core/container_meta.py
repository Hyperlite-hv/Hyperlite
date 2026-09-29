import json

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


# Application containers (see container_builder.read_image_config): the image, the process settings and the
# address given to the container, so that a clone or a restore can be defined the same way.


def set_container_app(container_name, image, spec, ip, network):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO container_apps (container_name, image, spec, ip, network) VALUES (?, ?, ?, ?, ?)",
            (container_name, image, json.dumps(spec), ip, network),
        )
        conn.commit()


def get_container_app(container_name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM container_apps WHERE container_name = ?", (container_name,)).fetchone()
    if not row:
        return None
    return {"image": row["image"], "spec": json.loads(row["spec"]), "ip": row["ip"], "network": row["network"]}


def delete_container_app(container_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM container_apps WHERE container_name = ?", (container_name,))
        conn.commit()
